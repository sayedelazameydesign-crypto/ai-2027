/* Shared, dependency-free cleanup contract for the direct Live Agent UI. */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AI2027CleanupStatus = api;
})(globalThis, function () {
  'use strict';

  var KNOWN = ['complete', 'not_applicable', 'partial', 'failed'];

  function assessCleanup(cleanup, runStatus) {
    var raw = cleanup && typeof cleanup.status === 'string' ? cleanup.status : '';
    var status = KNOWN.indexOf(raw) !== -1 ? raw : 'unreported';
    var directMode = status === 'not_applicable' && cleanup.reason === 'direct_live_agent_mode';
    var accepted = status === 'complete' || directMode;
    return {
      status: status,
      accepted: accepted,
      fullSuccess: runStatus === 'VERIFIED' && accepted
    };
  }

  return { assessCleanup: assessCleanup };
});
