// Before first paint, so a dark page doesn't flash white. Keep in step with src/lib/theme.ts.
// A file of its own, not inline in index.html, so the page can forbid inline scripts (Content-Security-Policy).
;(function () {
  var theme = null
  try {
    theme = localStorage.getItem('snapback-theme')
  } catch (e) {}
  var dark = theme === 'dark' || (theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
})()
