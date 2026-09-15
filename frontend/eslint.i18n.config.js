import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'

const untranslatedCopyRule = [
  'error',
  {
    selector: 'Literal[value=/[\\u3400-\\u9fff]/]',
    message: 'Do not hard-code Chinese UI copy. Add a shellI18n key and call t(...).',
  },
  {
    selector: 'TemplateElement[value.raw=/[\\u3400-\\u9fff]/]',
    message: 'Do not hard-code Chinese UI copy. Add a shellI18n key and call t(...).',
  },
  {
    selector: 'JSXText[value=/[A-Za-z\\u3400-\\u9fff]/]',
    message: 'Do not hard-code visible UI copy. Add a shellI18n key and call t(...).',
  },
  {
    selector: 'JSXAttribute[name.name=/^(aria-label|placeholder|title|alt)$/] > Literal[value=/[A-Za-z]/]',
    message: 'Do not hard-code user-facing attributes. Add a shellI18n key and call t(...).',
  },
]

export default [
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'src/i18n/**',
      'src/**/*.test.{ts,tsx}',
    ],
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: {
      'react-hooks': reactHooks,
    },
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        ecmaVersion: 2020,
        sourceType: 'module',
        ecmaFeatures: { jsx: true },
      },
    },
    rules: {
      'no-restricted-syntax': untranslatedCopyRule,
    },
  },
]
