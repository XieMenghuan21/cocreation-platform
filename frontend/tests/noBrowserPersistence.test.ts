import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, test } from 'vitest';
import { findBrowserPersistenceViolations } from './helpers/browserPersistenceAst';

const sourceFiles = (directory: string): string[] =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      return sourceFiles(path);
    }
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });

test('frontend business code contains no browser persistence APIs', () => {
  const violations = sourceFiles('src').flatMap((file) =>
    findBrowserPersistenceViolations(readFileSync(file, 'utf8'), file)
      .map((violation) => `${file}: ${violation}`),
  );

  expect(violations).toEqual([]);
});

describe('browser persistence AST guard', () => {
  test('detects element access and constant-concatenated property names', () => {
    const source = `
      const suffix = 'Storage';
      const key = 'local' + suffix;
      window[key].setItem('x', 'y');
      navigator['stor' + 'age'].getDirectory();
      globalThis['show' + 'OpenFilePicker']();
      Reflect.get(window, 'session' + 'Storage').setItem('a', 'b');
      Reflect.get(globalThis, 'caches').open('db');
    `;

    expect(findBrowserPersistenceViolations(source)).toEqual([
      'globalThis.caches',
      'globalThis.showOpenFilePicker',
      'navigator.storage',
      'window.localStorage',
      'window.sessionStorage',
    ]);
  });
});

test('frontend never persists or synthesizes a bearer token', () => {
  const violations = sourceFiles('src').filter((file) =>
    /\bAuthorization\b|Bearer\s+\$\{/.test(readFileSync(file, 'utf8')),
  );

  expect(violations).toEqual([]);
  expect(() => readFileSync('src/services/authStorage.ts', 'utf8')).toThrow();
});
