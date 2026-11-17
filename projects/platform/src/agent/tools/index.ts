// Central import: registering MVP tools (EXECUTION-ORDER-MVP.md §4).
// BROWSER_DISABLED=1 → browser.run is not registered at all (low-memory hosts
// like Render free 512MB): the planner never sees it, plans route via web.fetch.
import './shell-exec';
import './web-fetch';
if (process.env.BROWSER_DISABLED !== '1') {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  require('./browser-run');
}
import './fs-readwrite';

export { manifest, toolNames, getTool, runTool } from './registry';
export type { Tool, ToolContext } from './registry';
