// Apply a saved theme before the first paint so a dark theme does not flash white.
// A separate file, not inline, so the page can run under a strict Content-Security-Policy.
try {
  var theme = localStorage.getItem("rag.ui.theme");
  if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
} catch (error) {}
