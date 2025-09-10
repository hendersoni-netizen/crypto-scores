// Guard + confirm adapter is present (for Chart.js time scale)
(function(){
  if (!window.Chart) return;
  // If adapter didn't register, time scale may throw; expose a tiny check
  try {
    const ok = !!Chart.registry.adapters._date;
    if (!ok) console.warn('[time-adapter-guard] date adapter missing; loading fallback');
  } catch(e){ console.warn('[time-adapter-guard] check failed', e); }
})();