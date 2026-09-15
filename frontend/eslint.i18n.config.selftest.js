import assert from 'node:assert/strict'
import test from 'node:test'

import { ESLint } from 'eslint'

const eslint = new ESLint({
  cwd: import.meta.dirname,
  overrideConfigFile: 'eslint.i18n.config.js',
})

test('i18n lint rejects hard-coded Chinese UI copy', async () => {
  const [result] = await eslint.lintText(
    'export function Example() { return <button aria-label="保存">保存</button> }',
    { filePath: 'src/pages/I18nLintExample.tsx' },
  )

  assert.equal(result.errorCount, 2)
  assert.match(result.messages[0].message, /shellI18n/)
})

test('i18n lint rejects hard-coded English UI copy', async () => {
  const [result] = await eslint.lintText(
    'export function Example() { return <button aria-label="Save">Save changes</button> }',
    { filePath: 'src/pages/I18nLintExample.tsx' },
  )

  assert.equal(result.errorCount, 2)
  assert.match(result.messages[0].message, /shellI18n/)
})

test('i18n lint accepts translated UI copy', async () => {
  const [result] = await eslint.lintText(
    'export function Example({ t }) { return <button aria-label={t("save")}>{t("save")}</button> }',
    { filePath: 'src/pages/I18nLintExample.tsx' },
  )

  assert.equal(result.errorCount, 0)
})
