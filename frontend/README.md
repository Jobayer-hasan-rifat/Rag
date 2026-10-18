# Frontend

React 18, TypeScript (strict), Vite 6 and React Router 7. Styling is plain CSS (`src/styles/tokens.css` for the design tokens, `src/styles/global.css` for components). There is no UI framework and no CSS preprocessor.

## Running it

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173 (Vite dev server)
npm run typecheck      # tsc --noEmit, strict
npm test               # vitest (node environment; logic, client and renderer tests)
npm run build          # type check, then a production bundle in dist/
```

With `docker compose up` the frontend is served on http://localhost:5173 together with the API on http://localhost:8000.

### Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Origin of the API. `/api/v1` is appended. Read when Vite starts or builds. |

The API must list the frontend's origin in `CORS_ORIGINS`. The browser sends `Authorization`, `Content-Type`, `X-Request-ID` and `Idempotency-Key`; the API allows these and exposes `X-Request-ID`, `Content-Disposition` and `Retry-After`.

## Authentication

- The **access token lives in memory only**. The **refresh token is kept in `sessionStorage`** (never `localStorage`), so a session ends when the tab closes and a script on another origin cannot read it.
- On a reload the app shows a short "Restoring your session" screen while it exchanges the refresh token; protected routes render nothing until the session is confirmed, so there is no flash of protected content.
- A `401` triggers one refresh and one retry of the request. Refresh is single-flight because the backend rotates refresh tokens (two parallel refreshes would invalidate each other). If the refresh fails the session is cleared and the user is sent to the sign-in page with a "session expired" message.
- Signing out revokes the refresh token on the server and clears the browser state.
- Limitation: `sessionStorage` is readable by any script running in the page (an XSS would reach it). An HttpOnly, SameSite cookie would be stronger; it needs a backend change and is deferred. The app avoids raw HTML rendering to keep that risk low.

## Routes

| Route | Page |
|-------|------|
| `/login`, `/register` | Sign in and account creation (password rules shown before submitting) |
| `/` | Dashboard: documents needing attention, recent documents, recent conversations, upload |
| `/documents` | Upload (drag and drop or picker, progress, duplicates), library with filters, sorting and pagination |
| `/documents/:id` | Document details, version history, upload a new version, activate, retry, rename, download, delete |
| `/search` | Semantic or hybrid search, optional rerank, per-search document filter, source panel |
| `/workspace`, `/workspace/:conversationId` | Persistent RAG conversations with citations and a source panel |
| `/settings` | Account, API address and server health |

## Documents

States shown to the user: queued, reading file, splitting text, preparing index, indexing, ready, failed. A document is **not searchable until it is ready**, and the interface says so on every row that is still processing. Rows that are processing refresh themselves every few seconds while the tab is visible.

- **Upload**: files are checked by extension in the browser (the server validates content and size). They upload one at a time, with progress. An identical file that already exists is reported as a duplicate with a link to the existing document; nothing new is created.
- **Versions**: a new upload on the document page becomes a new version. The current version keeps serving search and answers until the new one is ready; a failed version changes nothing. A ready, non-active version can be made active again. The interface only shows the new state after the server confirms it (the list is reloaded; nothing is updated optimistically).
- **Delete** (document or version) and **restore** ask for confirmation. Retry is offered for failed versions.

## Search

Hybrid (meaning and keywords) is the default; "meaning only" is available. Reranking is optional and the interface explains when the server has it turned off. Results show the document, the version, the heading or page where known and the passage. **No raw score, similarity or logit is shown as a confidence.** No term highlighting is applied: the API returns no match offsets, and guessing them from the query would mark text that did not cause the match.

## Conversations and citations

- Create, list, rename, delete and paginate conversations. The thread loads the newest messages first and can load earlier ones.
- Every question is sent with an `Idempotency-Key`. The key, text and filters are kept in `sessionStorage` while a question is pending, so a retry after a network error reuses the same key and cannot create a duplicate question. After a reload the thread is reconciled with the server and polls while a question is still being answered. There is no streaming: the answer appears when the server has stored it.
- Handled explicitly: `CONVERSATION_BUSY`, `MESSAGE_IN_PROGRESS`, `IDEMPOTENCY_KEY_REUSED`, rate limits (with `Retry-After`), answering disabled, model unavailable or timed out, nothing relevant found.
- The document filter and rerank setting apply to the next question only and are not carried forward.
- Answers are rendered by a small Markdown renderer that builds React elements and never injects HTML. Links are limited to http, https and mailto and open with `rel="noopener noreferrer nofollow"`.
- `[S1]`-style markers become buttons that open the source panel. A citation always shows **its own version's text**. If a newer version is now active the panel says the answer used an earlier version; if the document was deleted it says the source is no longer available and shows no text; text from the active version is never shown for an older citation.

## Layout and accessibility

Responsive from about 360 px: the sidebar collapses (state remembered) and becomes an off-canvas drawer on small screens, tables become stacked cards, controls are at least 40 px high. Dialogs and drawers use the native `<dialog>` element (focus trapping, Escape to close). There is a skip link, visible focus styles, labelled inputs, `aria-live` regions for upload and toast messages, and `prefers-reduced-motion` turns transitions off. The font stack covers Latin and Bangla.

## Tests

`npm test` runs 26 tests: the API client (envelope, errors, refresh single-flight and retry), error mapping, the Markdown renderer (HTML and unsafe links are not interpreted), citation source rules and the pending-question store. There are no browser end-to-end or automated accessibility tests yet; the pages were checked by hand against a running stack.

## Known limitations

- No evaluation dashboard (the backend has no evaluation API); collections, sharing and permissions are not in the interface.
- Refresh token in `sessionStorage` rather than an HttpOnly cookie (see above).
- Only the extension is checked in the browser; the server validates content.
- No ESLint configuration; `tsc --strict` is the static check.
- No automated end-to-end, visual or accessibility test suite.
