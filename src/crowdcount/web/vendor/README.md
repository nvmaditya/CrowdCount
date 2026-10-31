# Vendored frontend libraries

Shipped with the package so the dashboard works without internet access and under a
strict Content-Security-Policy (`script-src 'self'`). Files are the unmodified minified
distributions, except that `sourceMappingURL` comments were removed.

| Library | Version | License | Source |
| --- | --- | --- | --- |
| Bootstrap | 5.3.8 | MIT (`bootstrap/LICENSE`) | https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/ |
| Bootstrap Icons | 1.13.2 | MIT (`bootstrap-icons/LICENSE`) | https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.2/font/ |
| Chart.js | 4.6.1 | MIT (`chart.js/LICENSE.md`) | https://cdn.jsdelivr.net/npm/chart.js@4.6.1/dist/ |
| Geist | 1.9.0 | OFL-1.1 (`geist/LICENSE.txt`) | https://cdn.jsdelivr.net/npm/geist@1.9.0/dist/fonts/ |
| Lucide icons | 1.66.0 | ISC (`lucide/LICENSE`) | https://cdn.jsdelivr.net/npm/lucide-static@1.66.0/icons/ (paths copied into `assets/js/icons.js`) |

To upgrade, download the same files for the new version and update this table.
