# Interface design

This describes the visual and interaction design of the web application: what it was based on, the decisions made, and how to keep it consistent. The implementation is plain CSS in `src/styles/` and React components in `src/components/` and `src/pages/`.

## What informed the design

Public pages of three document-chat products were read before deciding on a direction. Only what was actually opened is listed; signed-in interfaces were not available to inspect, so nothing here is based on how those screens look.

| Source | What was read | Principle taken from it |
|--------|---------------|-------------------------|
| NotebookLM, Google's announcement of its redesign | The article describing its three areas: Sources, Chat and Studio | Keep managing sources, asking and reading evidence in one place, and let the reader open a source next to the conversation rather than leaving it. |
| NotebookLM sign-in page | The public landing page only | Restraint: a short promise and one action. |
| AnythingLLM documentation, "Using documents in chat" | The page on attaching versus embedding documents | Be explicit about what a document is used for and for how long (here: *Ready* means searchable; the active version is the one used). Allow adding files from the chat itself. |
| ChatPDF home page | The public home page | Built-in citations anchor answers to the source, and many files can be questioned in one conversation. Getting a first answer should take very few steps. |

None of their layouts was copied. The application keeps its own structure and wording.

## Visual direction

A research notebook rather than an admin panel or a chat clone.

- **Surfaces** are warm white on a warm gray page; text is a cool slate; the single accent is a calm medium blue. Meaning colours (green, amber, red) are used only for status and always appear with a word and a shape (a check, a warning mark, a spinner).
- **Type** uses system fonts only (no web-font download). Page titles, the product name, answers and quoted source passages use a serif stack, which gives reading text a document feel; controls and labels use the system sans. Both stacks fall back to an installed Bengali face, and answer and question text uses `dir="auto"` so Bangla and English render correctly, including mixed text.
- **Questions and answers** are not chat bubbles. A question leads with a blue rule and bold text; the answer follows as prose with numbered source markers, and the sources it used are listed beneath like footnotes. Reading width is capped at about 68 characters.
- **Depth** is limited to hairline borders and a small shadow on floating layers (menus, dialogs, toasts).
- **Motion** is short (120 to 200 ms) and used to explain a change (a panel arriving, a dialog opening). `prefers-reduced-motion` turns it off.
- **Themes**: light, dark, or follow the device. The choice is stored in this browser only and applied before first paint so the dark theme does not flash white.

All values are tokens in `src/styles/tokens.css`. Components never use raw colours.

## Layout

**Navigation.** A slim top bar holds the product mark, four destinations (Home, Documents, Search, Workspace), the one primary action (*Add documents*) and an account menu (settings, theme, sign out). There is no wide sidebar, so the whole width is available to content. On phones the destinations move to a bottom tab bar within thumb reach and the top bar keeps only the mark, the upload action and the account. Inside a conversation on a phone the screen belongs to the thread and a back arrow returns to the list.

**Workspace (desktop).** Three regions: a conversation list (collapsible), the thread, and a source panel. Opening a source number in an answer shows the passage in a panel beside the conversation, which stays readable. The panel can be dragged or resized with the arrow keys (Home and End jump to the limits), closed, and its width is remembered. Below 1180 px the source opens as a drawer over the thread instead of squeezing it.

**Workspace (phone).** The thread fills the screen. The source opens as a bottom sheet that leaves the conversation visible above it. The composer is pinned to the bottom and the layout follows the visible viewport so the on-screen keyboard does not cover it (`interactive-widget=resizes-content` plus a `visualViewport` listener for browsers that ignore it). On touch devices Enter inserts a new line and the arrow button sends; on a keyboard Enter sends and Shift+Enter inserts a new line.

**Documents.** A compact table on larger screens; stacked rows with a status line on phones. Secondary actions (details, ask about it, download, delete) are in a menu on each row; *Retry* is shown directly when a document failed. Filters collapse behind a button on phones.

**Document details.** The top of the page states which version search and answers use, which version the page describes, and the state of the latest upload. Versions are listed newest first, each with a state word (Active, Not active, Processing, Failed), whether it is searchable and what can be done with it. Older answers keep pointing at the version they used and say so.

**Search.** One prominent field; method, result count, document filter and reranking sit behind an *Options* line that summarises the current choices. Results show the document, version and location, an excerpt in reading type, and three actions: view the source, open the document, and ask about that document (which opens a new conversation limited to it for the first question).

## Accessibility

Targeted at WCAG 2.2 AA.

- Text and control contrast is checked by `npm run check:contrast` (also run by `npm test`) for every text/background pair in both themes; input borders are held to 3:1.
- Focus is always visible (2 px ring). Dialogs and drawers use the native `<dialog>` element, so focus is trapped, Escape closes them and focus returns to the opener. Menus implement the menu-button pattern (arrow keys, Home, End, Escape).
- Every icon-only control has an accessible name. A skip link jumps to the content; moving to another section moves focus to the content and sets the tab title. Each page has one `h1` and no skipped heading levels.
- Status is never colour alone. Upload progress and results are announced through a polite live region, and a document that finishes processing or fails is announced on the library page.
- Touch devices get 44 px targets (the small citation marker extends its hit area to 44 px). Form fields use 16 px text so iOS does not zoom.
- Password fields have a show/hide control; field errors are associated with their field.

## Responsive behaviour

Breakpoints are 720 px (phone layout: bottom tabs, stacked rows, bottom sheets), 900 px (conversation list and thread become one screen at a time below it; the upload button loses its label on tablets) and 1180 px (the source panel docks beside the thread). Page content is capped at 72 rem so lines do not stretch on large monitors; the workspace uses the full window.

## Keeping it consistent

- Add colours, sizes and shadows to `tokens.css` first and run `npm run check:contrast`.
- Reuse `Button`, `IconButton`, `Menu`, `Dialog`, `Notice`, `StatusPill`, `Switch` and `PasswordInput` from `src/components/ui/`.
- Anything that says a document is *Ready* or *Active* must come from the API response, never from the interface's own assumption.
