// Source of ptf-fx.js: ThinkingOrb (thinking-orbs, MIT, libraries.dev) callable from vanilla JS.
// Built with Preact via preact/compat aliases (see the header of ptf-fx.js).
import React from 'react';
import { createRoot } from 'react-dom/client';
import { ThinkingOrb } from 'thinking-orbs';
function orb(el, opts = {}) {
  if (!el) return () => {};
  const root = createRoot(el);
  root.render(React.createElement(ThinkingOrb, { state: opts.state || 'composing', size: opts.size || 20, theme: opts.theme || 'auto' }));
  return () => root.unmount();
}
window.PTFFx = { orb };
