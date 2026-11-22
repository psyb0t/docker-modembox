const repository = "https://github.com/psyb0t/docker-modembox";

export default {
  title: "ModemBox",
  tagline: "USB modem control for SMS, calls, and AT commands",
  url: "https://psyb0t.github.io",
  baseUrl: "/docker-modembox/",
  trailingSlash: true,
  future: { faster: { rspackBundler: true, swcJsLoader: true } },
  organizationName: "psyb0t",
  projectName: "docker-modembox",
  onBrokenLinks: "throw",
  markdown: { format: "md", hooks: { onBrokenMarkdownLinks: "throw" } },
  staticDirectories: ["static", ".assets"],
  scripts: [{ src: "https://psyb0t.github.io/analytics.js", async: true }],
  presets: [
    ["classic", {
      blog: false,
      docs: {
        path: "../docs",
        routeBasePath: "/",
        sidebarPath: "./sidebars.mjs",
        editUrl: repository + "/edit/main/docs/",
      },
    }],
  ],
  themes: [
    ["@easyops-cn/docusaurus-search-local", {
      docsDir: "../docs",
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
  },
};
