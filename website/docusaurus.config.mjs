const repository = "https://github.com/psyb0t/docker-modembox";
const baseUrl = "/docker-modembox/";

export default {
  title: "ModemBox",
  tagline: "USB modem control for SMS, calls, and AT commands",
  url: "https://psyb0t.github.io",
  baseUrl,
  trailingSlash: true,
  future: { faster: { rspackBundler: true, swcJsLoader: true } },
  organizationName: "psyb0t",
  projectName: "docker-modembox",
  onBrokenLinks: "throw",
  markdown: { format: "detect", hooks: { onBrokenMarkdownLinks: "throw" } },
  staticDirectories: ["static", ".assets"],
  scripts: [{ src: "https://psyb0t.github.io/analytics.js", async: true }],
  presets: [
    ["classic", {
      blog: false,
      sitemap: { ignorePatterns: [baseUrl + "search", baseUrl + "search/"] },
      docs: {
        path: ".content",
        routeBasePath: "/",
        sidebarPath: "./sidebars.mjs",
        docItemComponent: "@theme/ApiItem",
        editUrl: ({ docPath }) => docPath.startsWith("api/")
          ? undefined
          : repository + "/edit/main/docs/" + docPath,
      },
    }],
  ],
  plugins: [
    ["docusaurus-plugin-openapi-docs", {
      id: "api",
      docsPluginId: "classic",
      config: {
        modembox: {
          specPath: ".assets/reference/openapi.json",
          outputDir: ".content/api",
          showInfoPage: false,
          hideSendButton: true,
          sidebarOptions: { groupPathsBy: "tag" },
        },
      },
    }],
  ],
  themes: [
    "docusaurus-theme-openapi-docs",
    ["@easyops-cn/docusaurus-search-local", {
      docsDir: ".content",
      docsRouteBasePath: "/",
      indexBlog: false,
      hashed: true,
    }],
  ],
  themeConfig: {
    navbar: {
      title: "ModemBox",
      items: [
        { href: repository, label: "GitHub", position: "right" },
        { href: repository + "/blob/main/CHANGELOG.md", label: "Changelog", position: "right" },
      ],
    },
    colorMode: { defaultMode: "dark", respectPrefersColorScheme: true },
    api: { schemaExpansion: { enabled: true, default: 1 } },
  },
};
