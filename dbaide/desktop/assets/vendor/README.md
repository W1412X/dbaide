# Bundled WebEngine scripts

Offline copies of front-end libraries used by chart and Markdown rendering.
Refreshed via `tools/download_vendor_assets.sh`.

| File | Package | Version |
|------|---------|---------|
| `marked.umd.js` | [marked](https://github.com/markedjs/marked) | 12.0.2 |
| `highlight.min.js` | [highlight.js](https://highlightjs.org/) | 11.9.0 |
| `echarts.min.js` | [Apache ECharts](https://echarts.apache.org/) | 5.6.0 |

Override at runtime with `DBAIDE_MARKED_SRC`, `DBAIDE_HLJS_SRC`, or `DBAIDE_ECHARTS_SRC`.
