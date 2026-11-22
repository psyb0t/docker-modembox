# ModemBox

ModemBox is a single operator cellular gateway. FastAPI serves REST, FastMCP 2, WebSockets and the SvelteKit static build. SQLite stores SMS and encrypted SIM PINs.

Read `.agents/rules/modems.md` before changing modem behavior. Run `make lint` and `make test` for code changes and `make build` for image or UI changes. Tests use pseudo terminals and do not need a SIM. Document public API changes in `docs/api.md`.

The Documentation workflow builds the Markdown in `docs/` with Docusaurus and publishes through the shared GitHub Pages workflow. Give each page a title and description, link between pages with relative Markdown paths, and build with `make docs-build`. Add new guides to `website/sidebars.mjs` and `website/static/llms.txt`.
