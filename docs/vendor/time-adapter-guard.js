// Guard + quick console note for the time adapter
(function(){
  const ok = !!(Chart && Chart._adapters && Chart._adapters.date);
  if (!ok) {
    console.warn("time-adapter-guard: date adapter is not fully registered. Check chartjs-adapter-date-fns import.");
  }
})();
