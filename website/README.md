# ModemBox documentation build

Run `make generate-docs` after changing a REST route or model. FastAPI exports the contract to `docs/reference/openapi.json`. Run `make docs-build` to check the export and generate the Pages site. Edit the guides in `docs/`.

`make docs-audit` reports [GHSA-qxc2-j82w-r537](https://github.com/advisories/GHSA-qxc2-j82w-r537) in Faker 5.5.3, pulled in by Postman's collection library. The patched major version removes APIs that Postman uses. The generator runs offline in a container against the schema exported from the checkout. The public reference uses fixed curl examples in place of Postman's browser request editor.
