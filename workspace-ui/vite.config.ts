import { createHash } from 'node:crypto';
import {
  closeSync,
  constants as fsConstants,
  fstatSync,
  lstatSync,
  openSync,
  readFileSync,
} from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv, type Plugin, type ViteDevServer } from 'vite';

const CONFIG_DIR = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(CONFIG_DIR, '..');
const DEFAULT_CREDENTIALS_FILE = resolve(REPO_ROOT, '.ember-up', 'api-credentials.json');
const READ_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const READ_SECRET_RE = /^[A-Za-z0-9_-]{32,256}$/;
const WRITE_SECRET_RE = /^\S{32,512}$/;
const SHA256_RE = /^[0-9a-f]{64}$/i;

interface GatewayCredentials {
  reader: string;
  operator: string;
}

function localTarget(raw: string | undefined, fallback: string, label: string): string {
  let url: URL;
  try {
    url = new URL(raw || fallback);
  } catch {
    throw new Error(`${label} must be an absolute loopback HTTP URL`);
  }
  if (
    url.protocol !== 'http:' || url.hostname !== '127.0.0.1' ||
    url.username || url.password || url.pathname !== '/' || url.search || url.hash
  ) {
    throw new Error(`${label} must target http://127.0.0.1 on the local machine`);
  }
  const port = Number(url.port || '80');
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${label} has an invalid port`);
  }
  return url.origin;
}

function localGatewayCredentials(filePath: string): GatewayCredentials | null {
  // POSIX owner-only permissions are the local trust boundary used by
  // ember_up.sh. On platforms where those modes cannot be verified, refuse.
  if (process.platform === 'win32' || typeof process.getuid !== 'function') return null;
  const noFollow = fsConstants.O_NOFOLLOW;
  if (typeof noFollow !== 'number') return null;

  let descriptor: number | undefined;
  try {
    const parent = lstatSync(dirname(filePath));
    const before = lstatSync(filePath);
    const owner = process.getuid();
    const isPrivateRegularFile = (info: ReturnType<typeof lstatSync>) =>
      info.isFile() && !info.isSymbolicLink() && info.uid === owner && info.nlink === 1 &&
      (info.mode & 0o077) === 0 && info.size > 0 && info.size <= 16_384;
    if (
      !parent.isDirectory() || parent.isSymbolicLink() || parent.uid !== owner ||
      (parent.mode & 0o077) !== 0 || !isPrivateRegularFile(before)
    ) return null;

    descriptor = openSync(filePath, fsConstants.O_RDONLY | noFollow);
    const after = fstatSync(descriptor);
    if (
      !isPrivateRegularFile(after) || before.dev !== after.dev || before.ino !== after.ino
    ) return null;
    const raw = readFileSync(descriptor, 'utf8');
    closeSync(descriptor);
    descriptor = undefined;

    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    const value = parsed as Record<string, unknown>;
    const readId = value.read_id;
    const readSecret = value.read_secret;
    const writeToken = value.write_token;
    const hashes = value.read_hashes;
    if (
      typeof readId !== 'string' || !READ_ID_RE.test(readId) ||
      typeof readSecret !== 'string' || !READ_SECRET_RE.test(readSecret) ||
      typeof writeToken !== 'string' || !WRITE_SECRET_RE.test(writeToken) ||
      !hashes || typeof hashes !== 'object' || Array.isArray(hashes)
    ) return null;

    const digest = createHash('sha256').update(readSecret, 'utf8').digest('hex');
    const configuredHash = (hashes as Record<string, unknown>)[readId];
    if (typeof configuredHash !== 'string' || !SHA256_RE.test(configuredHash) ||
        configuredHash.toLowerCase() !== digest) return null;
    return { reader: `${readId}.${readSecret}`, operator: writeToken };
  } catch {
    return null;
  } finally {
    if (descriptor !== undefined) {
      try { closeSync(descriptor); } catch { /* already closed */ }
    }
  }
}

function requestPath(req: import('node:http').IncomingMessage): string | null {
  try {
    return new URL(req.url || '/', 'http://127.0.0.1').pathname;
  } catch {
    return null;
  }
}

function isLoopbackHost(host: string | undefined, port: number): boolean {
  if (!host) return false;
  const value = host.toLowerCase();
  return value === `127.0.0.1:${port}` || value === `localhost:${port}`;
}

function sameLocalOrigin(
  req: import('node:http').IncomingMessage,
  port: number,
  required: boolean,
): boolean {
  const origin = req.headers.origin;
  if (origin === undefined) return !required;
  if (typeof origin !== 'string' || !isLoopbackHost(req.headers.host, port)) return false;
  try {
    const parsed = new URL(origin);
    return parsed.protocol === 'http:' && !parsed.username && !parsed.password &&
      parsed.pathname === '/' && !parsed.search && !parsed.hash &&
      parsed.host.toLowerCase() === req.headers.host?.toLowerCase() &&
      isLoopbackHost(parsed.host, port);
  } catch {
    return false;
  }
}

function reject(res: import('node:http').ServerResponse, status: number, error: string): void {
  const body = JSON.stringify({ error });
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Length', Buffer.byteLength(body));
  res.end(body);
}

function serverIsLoopback(server: ViteDevServer): boolean {
  const address = server.httpServer?.address();
  return Boolean(address && typeof address !== 'string' && address.address === '127.0.0.1');
}

function trustedLocalGateway(credentialsFile: string, hudOrigin: string): Plugin {
  return {
    name: 'ai2027-trusted-local-gateway',
    configureServer(server) {
      if (server.config.server.host !== '127.0.0.1' || server.config.server.https) {
        throw new Error('Workspace UI credential proxy is local-only: bind HTTP to 127.0.0.1');
      }
      const port = server.config.server.port;
      if (!Number.isInteger(port) || port < 1 || port > 65535) {
        throw new Error('Workspace UI credential proxy requires a fixed local port');
      }

      // This middleware runs before Vite's proxy middleware. It validates the
      // browser-facing authority/Origin, then injects credentials server-side.
      server.middlewares.use((req, res, next) => {
        const pathname = requestPath(req);
        if (pathname === null) return reject(res, 400, 'invalid request path');
        const isApi = pathname === '/api' || pathname.startsWith('/api/');
        const isOps = pathname === '/ops' || pathname.startsWith('/ops/');
        const isStudioApi = pathname === '/studio-api' || pathname.startsWith('/studio-api/');
        if (!isApi && !isOps && !isStudioApi) return next();

        const method = (req.method || '').toUpperCase();
        const originRequired = method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS';
        if (!serverIsLoopback(server)) return reject(res, 503, 'local gateway is not bound to loopback');
        if (!isLoopbackHost(req.headers.host, port) ||
            !sameLocalOrigin(req, port, originRequired)) {
          return reject(res, 403, 'local same-origin access required');
        }
        const fetchSite = req.headers['sec-fetch-site'];
        if (typeof fetchSite === 'string' && fetchSite.toLowerCase() === 'cross-site') {
          return reject(res, 403, 'cross-site request rejected');
        }

        // Agent Studio's relative proxy uses its own API boundary and no token
        // bridge here, but it still must not bypass the local Host/Origin guard.
        if (isStudioApi) return next();
        if (isOps) {
          if (method !== 'GET' && method !== 'POST') {
            return reject(res, 405, 'method not allowed');
          }
          return next();
        }

        if (method === 'GET' && pathname === '/api/health') return next();
        if (method === 'GET') {
          const credentials = localGatewayCredentials(credentialsFile);
          if (!credentials) return reject(res, 503, 'local reader credentials unavailable');
          req.headers.authorization = `Bearer ${credentials.reader}`;
          return next();
        }
        if (method === 'POST' && pathname === '/api/run') {
          const credentials = localGatewayCredentials(credentialsFile);
          if (!credentials) return reject(res, 503, 'local operator credentials unavailable');
          req.headers.authorization = `Bearer ${credentials.operator}`;
          return next();
        }
        return reject(res, 405, 'method not allowed');
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  // These values are server-only. No token is put in VITE_ env, define, or the
  // browser bundle; the local credential file is opened only for API requests.
  const env = loadEnv(mode, CONFIG_DIR, '');
  const liveTarget = localTarget(
    env.AI2027_LIVE_AGENT_URL, 'http://127.0.0.1:8090', 'AI2027_LIVE_AGENT_URL',
  );
  const hudTarget = localTarget(
    env.AI2027_COMMAND_HUD_URL, 'http://127.0.0.1:8091', 'AI2027_COMMAND_HUD_URL',
  );
  const credentialsFile = resolve(
    env.AI2027_LOCAL_API_CREDENTIALS_FILE || DEFAULT_CREDENTIALS_FILE,
  );

  return {
    plugins: [react(), trustedLocalGateway(credentialsFile, hudTarget)],
    server: {
      host: '127.0.0.1',
      port: 4173,
      strictPort: true,
      allowedHosts: ['127.0.0.1', 'localhost'],
      proxy: {
        '/api': {
          target: liveTarget,
          changeOrigin: true,
        },
        '/ops': {
          target: hudTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/ops/, '') || '/',
          configure: (proxy) => {
            // The HUD intentionally validates loopback Host and Origin. Rewrite
            // the already-validated browser Origin to the local upstream so its
            // strict local CSRF guard agrees with this trusted dev proxy.
            proxy.on('proxyReq', (proxyReq, req) => {
              if (typeof req.headers.origin === 'string') {
                proxyReq.setHeader('Origin', hudTarget);
              }
            });
          },
        },
        '/studio-api': {
          target: env.AI2027_STUDIO_URL || 'http://127.0.0.1:8080',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/studio-api/, '') || '/',
        },
      },
    },
  };
});
