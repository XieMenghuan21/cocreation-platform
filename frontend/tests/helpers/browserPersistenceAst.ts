import ts from 'typescript';

const forbiddenIdentifiers = new Set([
  'localStorage',
  'sessionStorage',
  'indexedDB',
  'caches',
  'Cache',
  'CacheStorage',
  'FileSystemHandle',
  'FileSystemFileHandle',
  'FileSystemDirectoryHandle',
]);

const forbiddenPaths = new Set([
  'document.cookie',
  'navigator.storage',
  'window.localStorage',
  'window.sessionStorage',
  'window.indexedDB',
  'window.caches',
  'globalThis.localStorage',
  'globalThis.sessionStorage',
  'globalThis.indexedDB',
  'globalThis.caches',
  'window.showOpenFilePicker',
  'window.showSaveFilePicker',
  'window.showDirectoryPicker',
  'globalThis.showOpenFilePicker',
  'globalThis.showSaveFilePicker',
  'globalThis.showDirectoryPicker',
]);

const forbiddenPickerNames = new Set([
  'showOpenFilePicker',
  'showSaveFilePicker',
  'showDirectoryPicker',
]);

const stringConstants = (sourceFile: ts.SourceFile): Map<string, string> => {
  const values = new Map<string, string>();
  const evaluate = (node: ts.Expression): string | null => {
    if (ts.isStringLiteralLike(node)) return node.text;
    if (ts.isIdentifier(node)) return values.get(node.text) ?? null;
    if (
      ts.isBinaryExpression(node)
      && node.operatorToken.kind === ts.SyntaxKind.PlusToken
    ) {
      const left = evaluate(node.left);
      const right = evaluate(node.right);
      return left === null || right === null ? null : left + right;
    }
    return null;
  };
  const visit = (node: ts.Node): void => {
    if (
      ts.isVariableDeclaration(node)
      && ts.isIdentifier(node.name)
      && node.initializer
    ) {
      const value = evaluate(node.initializer);
      if (value !== null) values.set(node.name.text, value);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return values;
};

export const findBrowserPersistenceViolations = (
  source: string,
  filename = 'source.ts',
): string[] => {
  const sourceFile = ts.createSourceFile(
    filename,
    source,
    ts.ScriptTarget.Latest,
    true,
    filename.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const constants = stringConstants(sourceFile);
  const violations = new Set<string>();

  const evaluateProperty = (node: ts.Expression): string | null => {
    if (ts.isStringLiteralLike(node)) return node.text;
    if (ts.isIdentifier(node)) return constants.get(node.text) ?? null;
    if (
      ts.isBinaryExpression(node)
      && node.operatorToken.kind === ts.SyntaxKind.PlusToken
    ) {
      const left = evaluateProperty(node.left);
      const right = evaluateProperty(node.right);
      return left === null || right === null ? null : left + right;
    }
    return null;
  };

  const expressionPath = (node: ts.Expression): string | null => {
    if (ts.isIdentifier(node)) return node.text;
    if (ts.isPropertyAccessExpression(node)) {
      const owner = expressionPath(node.expression);
      return owner ? `${owner}.${node.name.text}` : null;
    }
    if (ts.isElementAccessExpression(node) && node.argumentExpression) {
      const owner = expressionPath(node.expression);
      const property = evaluateProperty(node.argumentExpression);
      return owner && property ? `${owner}.${property}` : null;
    }
    if (
      ts.isCallExpression(node)
      && ts.isPropertyAccessExpression(node.expression)
      && ts.isIdentifier(node.expression.expression)
      && node.expression.expression.text === 'Reflect'
      && node.expression.name.text === 'get'
      && node.arguments.length >= 2
    ) {
      const owner = expressionPath(node.arguments[0] as ts.Expression);
      const property = evaluateProperty(node.arguments[1] as ts.Expression);
      return owner && property ? `${owner}.${property}` : null;
    }
    return null;
  };

  const visit = (node: ts.Node): void => {
    if (ts.isIdentifier(node) && forbiddenIdentifiers.has(node.text)) {
      violations.add(node.text);
    }
    if (
      ts.isPropertyAccessExpression(node)
      || ts.isElementAccessExpression(node)
      || ts.isCallExpression(node)
    ) {
      const expression = ts.isCallExpression(node) ? node : node;
      const path = expressionPath(expression);
      const property = path?.split('.').at(-1);
      if (
        path
        && (
          forbiddenPaths.has(path)
          || (property !== undefined && forbiddenPickerNames.has(property))
        )
      ) {
        violations.add(path);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return [...violations].sort();
};
