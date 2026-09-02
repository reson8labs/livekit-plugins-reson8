import { defineConfig } from 'vitest/config';

export default defineConfig({
  define: {
    __PACKAGE_NAME__: JSON.stringify('@reson8-labs/agents-plugin-reson8'),
    __PACKAGE_VERSION__: JSON.stringify('0.0.0-test'),
  },
  test: {
    include: ['src/**/*.test.ts'],
    testTimeout: 10_000,
    hookTimeout: 10_000,
  },
});
