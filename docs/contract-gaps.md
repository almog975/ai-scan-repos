# Contract gaps

Open questions where the UI needs more than `contracts/` defines. The contracts stay the source of truth; changes
go through a contracts PR.

## test-unsaved-stored-key

`POST /admin/ai-providers/test` (`testUnsavedProvider`) takes an `AIProviderWrite` body, which has no provider id.
When an admin edits an existing provider (unsaved changes) without re-typing the API key, the UI tests the form
body with `api_key: null`, so the server cannot use the stored, encrypted key; a key-protected provider then fails
the test even though saving would keep its key. Without unsaved changes the UI calls `testProvider(id)`, which does
use the stored key.

Options for the contracts owner: an optional `provider_id` (or `use_stored_key`) on the test body, or
`POST /admin/ai-providers/{id}/test` with an optional `AIProviderPatch` body. Until then the edit form tests
unsaved changes as-is (frontend/src/views/Admin.tsx, `runTest`).
