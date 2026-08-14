#!/usr/bin/env node
/**
 * persist-cordis-plugin.mjs
 *
 * Persists a dynamic Cordis plugin (defined via cordis_define) into the local
 * dsh profile so it survives process restarts. This is the "固化" step of the
 * install workflow: after a dynamic plugin is defined and run successfully,
 * run this script to register it permanently.
 *
 * It takes the SAME spec you passed to cordis_define — plugin id, name,
 * purpose, and the host/client function bodies — and generates:
 *
 *   <profile>/node_modules/<pkg-name>/package.json
 *   <profile>/node_modules/<pkg-name>/lib/index.js    (host half, ESM)
 *   <profile>/node_modules/<pkg-name>/lib/client.js   (browser half, __ModuleLoader__ bundle)
 *
 * and appends a loader row to <profile>/web/cordis.patch.yml so the host
 * Loader composes it (and dsh-client-modules serves the browser half) at the
 * next boot. Idempotent: re-running updates in place; --remove undoes.
 *
 * NOTE: the persisted plugin takes effect on the NEXT harness start. A plugin
 * with a browser half declares dsh.client.platform ("web") in its package.json
 * and is served to the browser automatically — no separate approval is needed
 * for composition rows.
 *
 * Usage:
 *   node scripts/persist-cordis-plugin.mjs --from-spec spec.json
 *   node scripts/persist-cordis-plugin.mjs --from-spec spec.json --check
 *   node scripts/persist-cordis-plugin.mjs --from-spec spec.json --remove
 *
 * spec.json shape (id is the row id; host/client are the cordis_define code
 * strings — function bodies that return the plugin object):
 * {
 *   "id": "openspec-mention",
 *   "name": "openspec-at-mention",
 *   "purpose": "one-line purpose (optional)",
 *   "host":   "return { inject: ['systemPrompt'], apply(ctx) { ... } }",
 *   "client": "return { inject: ['inputTriggers', ...], apply(ctx) { ... } }"
 * }
 *
 * Env overrides:
 *   DSH_PROFILES_ROOT   dsh profiles root (default ~/.dsh/profiles)
 */

import { execSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const PROFILES_ROOT = process.env.DSH_PROFILES_ROOT ?? join(homedir(), '.dsh', 'profiles');
const MODULES_ROOT = join(PROFILES_ROOT, 'node_modules');
const PATCH_FILE = join(PROFILES_ROOT, 'web', 'cordis.patch.yml');

function fail(message) {
	console.error(`[persist-cordis-plugin] ${message}`);
	process.exit(1);
}

function sanitizePackageName(id) {
	const cleaned = id.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '');
	if (cleaned.length === 0) fail(`cannot derive a package name from id "${id}"; pass an explicit "pkg" field`);
	return `dsh-${cleaned}`;
}

function parseSpec(path) {
	let spec;
	try {
		spec = JSON.parse(readFileSync(path, 'utf8'));
	} catch (error) {
		fail(`cannot read spec ${path}: ${error.message}`);
	}
	if (typeof spec !== 'object' || spec === null) fail('spec must be a JSON object');
	if (typeof spec.id !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(spec.id)) {
		fail(`spec.id must match [a-z0-9][a-z0-9-]* (got ${JSON.stringify(spec.id)})`);
	}
	if (typeof spec.host !== 'string' && typeof spec.client !== 'string') {
		fail('spec must provide at least one of host/client (function-body strings)');
	}
	return {
		id: spec.id,
		pkg: typeof spec.pkg === 'string' && /^[a-z0-9][a-z0-9-]*$/.test(spec.pkg) ? spec.pkg : sanitizePackageName(spec.id),
		name: typeof spec.name === 'string' && spec.name !== '' ? spec.name : spec.id,
		purpose: typeof spec.purpose === 'string' ? spec.purpose : '',
		host: typeof spec.host === 'string' ? spec.host : undefined,
		client: typeof spec.client === 'string' ? spec.client : undefined,
	};
}

/** Evaluate a cordis_define body ("return {...}") and read the plugin object's fields. */
function pluginFromBody(body) {
	// eslint-disable-next-line no-new-func
	const plugin = new Function(body)();
	if (typeof plugin !== 'object' || plugin === null) fail('host/client body must return a plugin object');
	return plugin;
}

/** Extract the body between the first '{' and the last '}' of a function source. */
function extractFunctionBody(fnSource) {
	const s = fnSource.trim();
	const open = s.indexOf('{');
	const close = s.lastIndexOf('}');
	if (open < 0 || close <= open) fail(`cannot extract function body from: ${s.slice(0, 60)}…`);
	return s.slice(open + 1, close);
}

function hostModuleSource(spec, plugin) {
	const inject = Array.isArray(plugin.inject) ? JSON.stringify(plugin.inject) : '[]';
	if (typeof plugin.apply !== 'function') fail('host body returned no apply function');
	const applySource = plugin.apply.toString();
	const isAsync = /^\s*async\b/.test(applySource);
	const body = extractFunctionBody(applySource);
	return [
		`/** Persisted by scripts/persist-cordis-plugin.mjs — host half of "${spec.name}". */`,
		`const name = ${JSON.stringify(spec.id)};`,
		`const inject = ${inject};`,
		'',
		`${isAsync ? 'async ' : ''}function apply(ctx) {`,
		body,
		'}',
		'',
		'export { apply, inject, name };',
		'',
	].join('\n');
}

function clientModuleSource(spec, clientBody) {
	return `window.__ModuleLoader__.load({
	id: ${JSON.stringify(spec.pkg)},
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		var __plugin = (() => {
${clientBody}
		})();
		exports.apply = __plugin.apply;
		exports.inject = __plugin.inject;
		return module.exports;
	}
});
`;
}

function packageJsonSource(spec, hasHost, hasClient) {
	const pkg = {
		name: spec.pkg,
		version: '0.1.0',
		description: spec.purpose || `Persisted Cordis plugin "${spec.name}".`,
		private: true,
		type: 'module',
	};
	if (hasHost) {
		pkg.main = 'lib/index.js';
		pkg.exports = { '.': './lib/index.js', './package.json': './package.json' };
	}
	if (hasClient) {
		pkg.exports = { ...(pkg.exports ?? {}), './client': './lib/client.js' };
		pkg.dsh = { client: { platform: 'web' } };
	}
	return JSON.stringify(pkg, null, 2) + '\n';
}

function rowBlock(spec) {
	return [
		`# persisted-plugin:${spec.id} (managed by scripts/persist-cordis-plugin.mjs)`,
		'- insert:',
		`    - id: ${spec.id}`,
		`      name: ${spec.pkg}`,
		'',
	].join('\n');
}

function patchHasRow(patchText, id) {
	return patchText.includes(`- insert:`) && patchText.includes(`- id: ${id}\n`);
}

function writePackage(spec, pluginHostObj, clientBody) {
	const pkgDir = join(MODULES_ROOT, spec.pkg);
	mkdirSync(join(pkgDir, 'lib'), { recursive: true });
	writeFileSync(join(pkgDir, 'package.json'), packageJsonSource(spec, pluginHostObj !== undefined, clientBody !== undefined), 'utf8');
	if (pluginHostObj !== undefined) writeFileSync(join(pkgDir, 'lib', 'index.js'), hostModuleSource(spec, pluginHostObj), 'utf8');
	if (clientBody !== undefined) writeFileSync(join(pkgDir, 'lib', 'client.js'), clientModuleSource(spec, clientBody), 'utf8');
	return pkgDir;
}

function updatePatch(spec, remove) {
	if (!existsSync(PATCH_FILE)) fail(`patch file not found: ${PATCH_FILE}`);
	let text = readFileSync(PATCH_FILE, 'utf8');
	const block = rowBlock(spec);
	if (remove) {
		if (!patchHasRow(text, spec.id)) return 'row absent';
		let start = text.indexOf(`# persisted-plugin:${spec.id}`);
		let end = text.length;
		if (start >= 0) {
			const nextHeading = text.indexOf('\n# ', start);
			if (nextHeading >= 0) end = nextHeading;
		} else {
			// Row exists without a marker comment: remove the insert block that owns it.
			const rowIndex = text.indexOf(`- id: ${spec.id}`);
			start = text.lastIndexOf('- insert:', rowIndex);
			if (start < 0) return 'row present but block not found; edit manually';
			const nextEntry = text.indexOf('- insert:', start + 1);
			const nextRow = text.indexOf('- id:', start + 1);
			end = Math.min(nextEntry < 0 ? text.length : nextEntry, nextRow < 0 ? text.length : nextRow);
		}
		text = text.slice(0, start) + text.slice(end).replace(/^\n+/, '');
		writeFileSync(PATCH_FILE, text.replace(/\n{3,}/g, '\n\n'), 'utf8');
		return 'row removed';
	}
	if (patchHasRow(text, spec.id)) return 'row present';
	// Append at end, after any trailing blank lines.
	text = text.replace(/\s+$/, '') + '\n\n' + block;
	writeFileSync(PATCH_FILE, text, 'utf8');
	return 'row added';
}

function verifyResolution(spec) {
	try {
		const resolved = execSync(`node -e "console.log(require.resolve('${spec.pkg}/package.json'))"`, {
			encoding: 'utf8',
			cwd: PROFILES_ROOT,
		}).trim();
		return resolved;
	} catch {
		return undefined;
	}
}

function main() {
	const args = process.argv.slice(2);
	const specArg = args.find((a) => a.startsWith('--from-spec=')) ?? args[args.indexOf('--from-spec') + 1];
	const mode = args.includes('--remove') ? 'remove' : args.includes('--check') ? 'check' : 'apply';
	if (specArg === undefined) fail('missing --from-spec <spec.json>');
	const spec = parseSpec(specArg);

	if (mode === 'check') {
		const pkgDir = join(MODULES_ROOT, spec.pkg);
		const exists = existsSync(join(pkgDir, 'package.json'));
		const patchText = existsSync(PATCH_FILE) ? readFileSync(PATCH_FILE, 'utf8') : '';
		const row = patchHasRow(patchText, spec.id);
		console.log(`package ${spec.pkg}: ${exists ? 'present' : 'absent'}`);
		console.log(`patch row ${spec.id}: ${row ? 'present' : 'absent'}`);
		console.log(`resolve: ${verifyResolution(spec) ?? 'NOT RESOLVABLE (restart required?)'}`);
		process.exit(exists && row ? 0 : 2);
	}

	const pluginHost = spec.host === undefined ? undefined : pluginFromBody(spec.host);
	if (mode === 'remove') {
		const pkgDir = join(MODULES_ROOT, spec.pkg);
		if (existsSync(pkgDir)) rmSync(pkgDir, { recursive: true, force: true });
		const rowState = updatePatch(spec, true);
		console.log(`removed package ${spec.pkg} (${existsSync(pkgDir) ? 'still present' : 'gone'}); patch row: ${rowState}`);
		process.exit(0);
	}

	const pkgDir = writePackage(spec, pluginHost, spec.client);
	const rowState = updatePatch(spec, false);
	console.log(`wrote package: ${pkgDir}`);
	console.log(`patch row ${spec.id}: ${rowState}`);
	const resolved = verifyResolution(spec);
	console.log(`resolve: ${resolved ?? 'not resolvable yet (start the harness from this profile to pick it up)'}`);
	console.log(`\nPersisted "${spec.name}" (${spec.id}). It activates on the NEXT harness start.`);
	console.log(`Verify afterwards with: node scripts/persist-cordis-plugin.mjs --from-spec ${specArg} --check`);
	process.exit(0);
}

main();
