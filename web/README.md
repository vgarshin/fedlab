# fedlab web — a first-React guide

This front end is deliberately small. Read the files in this order; each has comments that
explain the React idea it uses.

| Idea | Where to see it | One-sentence version |
|---|---|---|
| Component | `components/Layout.tsx`, every page | A function that returns markup (JSX); `<Layout>…</Layout>` nests content via `children`. |
| Props | `StatusBadge.tsx`, `Layout.tsx` | The inputs of a component, like function arguments. Read-only. |
| State (`useState`) | `RunsPage.tsx` (form fields) | A value React remembers; changing it re-renders the component. |
| Effects (`useEffect`) | `RunPage.tsx` (`useRunEvents`) | Code that talks to the outside world (open a connection) and cleans up after itself. |
| Server data (`useQuery`) | `App.tsx`, `RunsPage.tsx` | "Fetch this, cache it, tell me loading / error / data." Library: TanStack Query. |
| Writes (`useMutation`) | `RunsPage.tsx`, `RunPage.tsx` | A POST/PATCH; on success we *invalidate* cached queries so they refetch. |
| Routing | `App.tsx`, `RunPage.tsx` | The URL decides which page component is shown; `:runId` is read with `useParams()`; after an action `useNavigate()` can move to another page (RunPage goes to the Runs list after deleting the run). |
| Custom hook | `useRunEvents`, `useArtifact` | Your own `useSomething` function that reuses other hooks. |
| Live updates | `RunPage.tsx` | Server-Sent Events: the server pushes "something changed", we refetch. |
| Types | `types.ts`, `api.ts` | TypeScript describes the JSON shapes; the compiler catches typos before runtime. |

## Mental model

```
 URL ──► <Routes> picks a page component
              │ useQuery("/api/…")  ──►  api.ts (fetch + cookie + CSRF header)  ──► FastAPI
              │ useState(...)       ──►  form values the user is typing
              ▼
        JSX describes the screen for the CURRENT data; when data or state changes,
        React calls the function again and updates only what differs.
```

You never "update the screen" by hand — you change data/state and the screen follows.

## Commands (no Node on the host; everything runs in Docker)

```bash
docker compose up --build                # dev: API + worker + DB + Vite dev server on http://localhost:5173
docker run --rm -u 1000:1000 -e HOME=/tmp -v "$PWD":/web -w /web node:22-alpine npm run build   # typecheck + build
```

In dev, Vite proxies `/api` to the API container; in production nginx serves `dist/` and does the same proxying.
Login in dev: open `http://localhost:5173/api/auth/dev-login?email=vgarshin@yandex.ru` (works only when `env` is `dev`).
