// Generates src/client from the control plane's committed OpenAPI document (A1).
// The generator is pinned in package.json; CI regenerates and fails on any diff.
import { defineConfig } from '@hey-api/openapi-ts';

export default defineConfig({
  input: '../controlplane/openapi/admin-api.json',
  output: {
    path: 'src/client',
  },
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk'],
});
