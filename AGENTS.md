# ModemBox

ModemBox is a single operator cellular gateway. FastAPI serves REST, FastMCP 2, WebSockets and the SvelteKit static build. SQLite stores SMS and encrypted SIM PINs.

Read `.agents/rules/modems.md` before changing modem behavior. Run `make lint` and `make test` for code changes and `make build` for image or UI changes. Tests use pseudo terminals and do not need a SIM. Document public API changes in `docs/api.md`.

GitHub Pages builds the Markdown in `docs/`. Keep the front matter on each page, link between pages with relative Markdown paths, and run `make docs-build` after editing the site.
