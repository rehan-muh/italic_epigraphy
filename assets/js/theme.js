/* theme.js - the light/dark switch. The preference is applied in the document
 * head before first paint; this wires the control and stores the choice.
 * With nothing stored the site follows the operating system. */
(function () {
  'use strict';

  var KEY = 'atlas-theme';
  var root = document.documentElement;

  function current() {
    var set = root.getAttribute('data-theme');
    if (set === 'light' || set === 'dark') return set;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark' : 'light';
  }

  function label(button, mode) {
    var next = mode === 'dark' ? 'light' : 'dark';
    button.textContent = next === 'dark' ? 'Dark' : 'Light';
    button.setAttribute('aria-label', 'Switch to the ' + next + ' theme');
  }

  document.addEventListener('DOMContentLoaded', function () {
    var button = document.getElementById('theme-toggle');
    if (!button) return;

    label(button, current());
    button.hidden = false;

    button.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* not storable */ }
      label(button, next);
      // the map holds paint values of its own that are read from the tokens
      window.dispatchEvent(new CustomEvent('atlas:themechange', { detail: next }));
    });
  });
})();
