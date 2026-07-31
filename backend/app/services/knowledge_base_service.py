"""工业设计知识库服务：从产业共享平台同步知识切片，向量化入库 Milvus，并提供检索。"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from openai import OpenAI

from app.config.settings import settings

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """Milvus 向量入库与语义检索（复用产业共享平台 V8 知识数据）。"""

    def __init__(self) -> None:
        self._milvus_client: Any | None = None
        self._embed_client: Any | None = None
        self._embed_key = (
            settings.DASHSCOPE_API_KEY or settings.QWEN_API_KEY or ""
        ).strip()

    @property
    def milvus_uri(self) -> str:
        return (settings.KNOWLEDGE_MILVUS_URI or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.milvus_uri and self._embed_key)

    def _milvus(self) -> Any:
        if self._milvus_client is None:
            from pymilvus import MilvusClient

            self._milvus_client = MilvusClient(uri=self.milvus_uri)
        return self._milvus_client

    def _embed(self) -> Any:
        if self._embed_client is None:
            self._embed_client = OpenAI(
                api_key=self._embed_key,
                base_url=settings.VECTOR_DB_EMBEDDING_BASE_URL
                or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        return self._embed_client

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        client = self._embed()
        result = client.embeddings.create(
            model=settings.KNOWLEDGE_EMBEDDING_MODEL,
            input=texts,
        )
        ordered = sorted(result.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]

    @staticmethod
    def _stable_pk(*parts: object) -> int:
        digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
        return int(digest[:14], 16)  # 限制在 int64 范围内

    def _ensure_collection(self) -> None:
        client = self._milvus()
        collection = settings.KNOWLEDGE_COLLECTION
        if client.has_collection(collection):
            return
        client.create_collection(
            collection_name=collection,
            dimension=int(settings.KNOWLEDGE_EMBEDDING_DIM),
            metric_type="COSINE",
        )
        logger.info("已创建 Milvus 集合 %s", collection)

    def health(self) -> dict[str, object]:
        if not self.enabled:
            return {"status": "disabled", "message": "知识库未启用（需配置 MILVUS 与 embedding key）"}
        try:
            client = self._milvus()
            collection = settings.KNOWLEDGE_COLLECTION
            return {
                "status": "connected",
                "collection": collection,
                "collectionReady": client.has_collection(collection),
                "milvusUri": self.milvus_uri,
            }
        except Exception as exc:
            logger.warning("知识库健康检查失败: %s", exc)
            return {"status": "error", "message": str(exc)}

    def sync_from_v8(
        self,
        *,
        source_url: str,
        db_name: str,
        user: str,
        password: str,
        limit: int | None = None,
    ) -> dict[str, object]:
        """从 V8 MySQL 同步 knowledge_sources + chunks 并向量化入库。"""
        import pymysql

        if not self.enabled:
            return {"enabled": False, "upserted": 0, "reason": "knowledge service disabled"}
        try:
            conn = pymysql.connect(
                host=source_url.split(":")[0],
                port=int(source_url.split(":")[1]) if ":" in source_url else 3306,
                user=user,
                password=password,
                database=db_name,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10,
                read_timeout=30,
            )
        except Exception as exc:
            logger.warning("连接 V8 知识库失败: %s", exc)
            return {"enabled": True, "upserted": 0, "error": str(exc)}

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT source_id, source_type, title, content, visibility, status "
                    "FROM knowledge_sources WHERE status='ready'"
                )
                sources = cursor.fetchall()
                cursor.execute(
                    "SELECT chunk_id, source_id, chunk_index, content, summary "
                    "FROM knowledge_chunks"
                )
                chunks = cursor.fetchall()
        finally:
            conn.close()

        source_by_id = {str(item["source_id"]): item for item in sources}
        rows: list[dict[str, object]] = []
        batch: list[dict[str, object]] = []
        for chunk in chunks:
            source = source_by_id.get(str(chunk["source_id"]))
            title = source.get("title") if source else str(chunk["source_id"])
            content = str(chunk.get("content") or "").strip()
            if not content:
                continue
            batch.append({
                "chunk_id": str(chunk["chunk_id"]),
                "source_id": str(chunk["source_id"]),
                "title": title[:500] if title else str(chunk["source_id"]),
                "content": content[:16000],
                "source_type": source.get("source_type") if source else None,
                "visibility": source.get("visibility") if source else None,
            })
            if len(batch) >= 8:
                rows.extend(self._embed_and_stage(batch))
                batch = []
        if batch:
            rows.extend(self._embed_and_stage(batch))

        self._ensure_collection()
        client = self._milvus()
        for start in range(0, len(rows), 100):
            client.upsert(
                collection_name=settings.KNOWLEDGE_COLLECTION,
                data=rows[start : start + 100],
            )
        return {
            "enabled": True,
            "synced_sources": len(sources),
            "synced_chunks": len(chunks),
            "upserted": len(rows),
            "collection": settings.KNOWLEDGE_COLLECTION,
        }

    def _embed_and_stage(self, batch: list[dict[str, object]]) -> list[dict[str, object]]:
        texts = [str(item["content"]) for item in batch]
        try:
            embeddings = self._embed_texts(texts)
        except Exception as exc:
            logger.warning("向量化失败（跳过本批 %d 条）: %s", len(batch), exc)
            return []
        rows: list[dict[str, object]] = []
        for item, vector in zip(batch, embeddings):
            rows.append({
                "id": self._stable_pk(item["chunk_id"]),
                "chunk_id": item["chunk_id"],
                "source_id": item["source_id"],
                "title": item["title"],
                "content": item["content"],
                "metadata": {
                    "sourceType": item.get("source_type"),
                    "visibility": item.get("visibility"),
                    "sourceTitle": item.get("title"),
                },
                "vector": vector,
            })
        return rows

    def search(self, question: str, *, top_k: int = 5, score_threshold: float = 0.0) -> list[dict[str, object]]:
        """语义检索知识库，返回命中的切片。"""
        if not self.enabled:
            return []
        try:
            query_vectors = self._embed_texts([question])
        except Exception as exc:
            logger.warning("检索向量化失败: %s", exc)
            return []
        self._ensure_collection()
        client = self._milvus()
        results = client.search(
            collection_name=settings.KNOWLEDGE_COLLECTION,
            data=query_vectors,
            limit=top_k,
            output_fields=["chunk_id", "source_id", "title", "content", "metadata"],
            search_params={"metric_type": "COSINE", "params": {}},
        )
        citations: list[dict[str, object]] = []
        if not results:
            return citations
        for hit in results[0]:
            entity = hit.get("entity") or {}
            score = float(hit.get("distance") or 0.0)
            if score_threshold and score < score_threshold:
                continue
            citations.append({
                "chunkId": entity.get("chunk_id"),
                "sourceId": entity.get("source_id"),
                "title": entity.get("title"),
                "content": entity.get("content"),
                "score": round(score, 4),
            })
        return citations

    def build_context(
        self,
        question: str,
        *,
        top_k: int = 3,
    ) -> str:
        """检索并把命中内容组装为注入 prompt 的上下文文本。"""
        citations = self.search(question, top_k=top_k)
        if not citations:
            return ""
        sections = [
            f"[资料 {index + 1}]《{item.get('title') or '未命名资料'}》\n{item.get('content')}"
            for index, item in enumerate(citations)
        ]
        return "\n\n".join(sections)


knowledge_base_service = KnowledgeBaseService()
