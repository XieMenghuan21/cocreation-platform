from __future__ import annotations

import re

import pytest

from app.services.image_prompt_optimizer_service import ImagePromptOptimizerService


@pytest.mark.asyncio
async def test_design_board_prompt_requires_multi_view_2d_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ImagePromptOptimizerService()
    monkeypatch.setattr(service, "dashscope_base_url", None)
    monkeypatch.setattr(service, "dashscope_api_key", None)
    monkeypatch.setattr(service, "nodapi_base_url", None)
    monkeypatch.setattr(service, "nodapi_api_key", None)

    result = await service.optimize(prompt="生成布艺沙发设计图，要有上视图和下视图")

    assert "上视图" in result["optimizedPrompt"]
    assert "下视图" in result["optimizedPrompt"]
    assert "二维设计图板" in result["optimizedPrompt"]
    assert "top view" in result["comfyuiPrompt"]
    assert "bottom view" in result["comfyuiPrompt"]
    assert "2D technical design sheet" in result["comfyuiPrompt"]
    assert not re.search(r"[\u4e00-\u9fff]", result["comfyuiPrompt"])


@pytest.mark.asyncio
async def test_2d_plan_prompt_is_reference_locked_multiview_drawing(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ImagePromptOptimizerService()
    monkeypatch.setattr(service, "dashscope_base_url", None)
    monkeypatch.setattr(service, "dashscope_api_key", None)
    monkeypatch.setattr(service, "nodapi_base_url", None)
    monkeypatch.setattr(service, "nodapi_api_key", None)

    result = await service.optimize(prompt="基于参考图给三层冰箱生成2D平面图，要正面侧面上面下面和尺寸")

    assert "2D 平面图" in result["optimizedPrompt"]
    assert "基于参考图" in result["optimizedPrompt"]
    assert "正视图" in result["optimizedPrompt"]
    assert "下视图" in result["optimizedPrompt"]
    assert "不要重新设计" in result["optimizedPrompt"]
    assert "reference-locked 2D orthographic multi-view engineering drawing" in result["comfyuiPrompt"]
    assert "front view" in result["comfyuiPrompt"]
    assert "bottom view" in result["comfyuiPrompt"]
    assert "no product redesign" in result["comfyuiPrompt"]
    assert not re.search(r"[\u4e00-\u9fff]", result["comfyuiPrompt"])


@pytest.mark.asyncio
async def test_design_board_prompt_requires_six_views_and_not_single_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ImagePromptOptimizerService()
    monkeypatch.setattr(service, "dashscope_base_url", None)
    monkeypatch.setattr(service, "dashscope_api_key", None)
    monkeypatch.setattr(service, "nodapi_base_url", None)
    monkeypatch.setattr(service, "nodapi_api_key", None)

    result = await service.optimize(prompt="给三层冰箱生成设计图，要上下左右视图和尺寸标注")

    assert "正视图" in result["optimizedPrompt"]
    assert "后视图" in result["optimizedPrompt"]
    assert "上视图" in result["optimizedPrompt"]
    assert "下视图" in result["optimizedPrompt"]
    assert "front view" in result["comfyuiPrompt"]
    assert "rear view" in result["comfyuiPrompt"]
    assert "top view" in result["comfyuiPrompt"]
    assert "bottom view" in result["comfyuiPrompt"]
    assert "not a single top-down floor plan" in result["comfyuiPrompt"]
    assert not re.search(r"[\u4e00-\u9fff]", result["comfyuiPrompt"])


@pytest.mark.asyncio
async def test_fake_3d_prompt_is_routed_to_2d_isometric_render(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ImagePromptOptimizerService()
    monkeypatch.setattr(service, "dashscope_base_url", None)
    monkeypatch.setattr(service, "dashscope_api_key", None)
    monkeypatch.setattr(service, "nodapi_base_url", None)
    monkeypatch.setattr(service, "nodapi_api_key", None)

    result = await service.optimize(prompt="给这个产品生成3D模型效果图")

    assert "二维立体效果图" in result["optimizedPrompt"]
    assert "等轴测" in result["optimizedPrompt"]
    assert "不生成真实网格模型" in result["optimizedPrompt"]
    assert "2D image" in result["comfyuiPrompt"]
    assert "faux 3D" in result["comfyuiPrompt"]
    assert "no real 3D mesh" in result["comfyuiPrompt"]
    assert not re.search(r"[\u4e00-\u9fff]", result["comfyuiPrompt"])


@pytest.mark.asyncio
async def test_poster_prompt_uses_reference_product_cutout_composite(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ImagePromptOptimizerService()
    monkeypatch.setattr(service, "dashscope_base_url", None)
    monkeypatch.setattr(service, "dashscope_api_key", None)
    monkeypatch.setattr(service, "nodapi_base_url", None)
    monkeypatch.setattr(service, "nodapi_api_key", None)

    result = await service.optimize(prompt="基于这张产品图生成宣传海报", images=["https://example.com/product.png"])

    assert "图片编辑" in result["optimizedPrompt"]
    assert "抠出参考图原产品" in result["optimizedPrompt"]
    assert "海报版式" in result["optimizedPrompt"]
    assert "do not redesign" in result["comfyuiPrompt"]
    assert "cut out the exact reference product" in result["comfyuiPrompt"]
    assert "advertising poster layout" in result["comfyuiPrompt"]
    assert not re.search(r"[\u4e00-\u9fff]", result["comfyuiPrompt"])


@pytest.mark.asyncio
async def test_phone_stand_poster_prompt_adds_realistic_support_constraints(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ImagePromptOptimizerService()
    monkeypatch.setattr(service, "dashscope_base_url", None)
    monkeypatch.setattr(service, "dashscope_api_key", None)
    monkeypatch.setattr(service, "nodapi_base_url", None)
    monkeypatch.setattr(service, "nodapi_api_key", None)

    result = await service.optimize(prompt="兔子手机支架宣传海报，手机放在支架上", images=["https://example.com/stand.png"])

    assert "手机支架常识" in result["optimizedPrompt"]
    assert "手机不能穿模" in result["optimizedPrompt"]
    assert "phone stand" in result["comfyuiPrompt"]
    assert "phone rests on the cradle" in result["comfyuiPrompt"]
    assert "no intersection" in result["comfyuiPrompt"]
    assert "SUBJECT LOCK" in result["comfyuiPrompt"]
    assert "PHYSICAL PLAUSIBILITY" in result["comfyuiPrompt"]
    assert "CAMERA AND LENS" in result["comfyuiPrompt"]
    assert len(result["comfyuiPrompt"]) >= 1200
    assert not re.search(r"[\u4e00-\u9fff]", result["comfyuiPrompt"])


@pytest.mark.asyncio
async def test_common_sense_constraints_apply_to_multiple_product_types(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ImagePromptOptimizerService()
    monkeypatch.setattr(service, "dashscope_base_url", None)
    monkeypatch.setattr(service, "dashscope_api_key", None)
    monkeypatch.setattr(service, "nodapi_base_url", None)
    monkeypatch.setattr(service, "nodapi_api_key", None)

    sofa = await service.optimize(prompt="三人位布艺沙发宣传海报", images=["https://example.com/sofa.png"])
    power = await service.optimize(prompt="户外储能电源宣传海报", images=["https://example.com/power.png"])
    lamp = await service.optimize(prompt="桌面台灯宣传海报", images=["https://example.com/lamp.png"])

    assert "座椅/沙发常识" in sofa["optimizedPrompt"]
    assert "seat cushions aligned" in sofa["comfyuiPrompt"]
    assert "backrest connected" in sofa["comfyuiPrompt"]
    assert "电子设备常识" in power["optimizedPrompt"]
    assert "ports aligned on the front panel" in power["comfyuiPrompt"]
    assert "screen and buttons flush with the housing" in power["comfyuiPrompt"]
    assert "灯具常识" in lamp["optimizedPrompt"]
    assert "light source located inside the lamp head or shade" in lamp["comfyuiPrompt"]
    assert "stable base and visible support arm" in lamp["comfyuiPrompt"]


@pytest.mark.asyncio
async def test_exploded_prompt_uses_flat_2d_assembly_template(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ImagePromptOptimizerService()
    monkeypatch.setattr(service, "dashscope_base_url", None)
    monkeypatch.setattr(service, "dashscope_api_key", None)
    monkeypatch.setattr(service, "nodapi_base_url", None)
    monkeypatch.setattr(service, "nodapi_api_key", None)

    result = await service.optimize(prompt="做一张产品爆炸图，展示拆解结构")

    assert "平面爆炸拆解图" in result["optimizedPrompt"]
    assert "零件分离清晰" in result["optimizedPrompt"]
    assert "flat 2D exploded assembly diagram" in result["comfyuiPrompt"]
    assert "components separated" in result["comfyuiPrompt"]
    assert not re.search(r"[\u4e00-\u9fff]", result["comfyuiPrompt"])


@pytest.mark.asyncio
async def test_lighting_effect_prompt_adds_precise_light_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ImagePromptOptimizerService()
    monkeypatch.setattr(service, "dashscope_base_url", None)
    monkeypatch.setattr(service, "dashscope_api_key", None)
    monkeypatch.setattr(service, "nodapi_base_url", None)
    monkeypatch.setattr(service, "nodapi_api_key", None)

    result = await service.optimize(prompt="给产品加一个蓝色科技光效")

    assert "光效" in result["optimizedPrompt"]
    assert "边缘轮廓光" in result["optimizedPrompt"]
    assert "blue technology glow" in result["comfyuiPrompt"]
    assert "rim light" in result["comfyuiPrompt"]
    assert not re.search(r"[\u4e00-\u9fff]", result["comfyuiPrompt"])
