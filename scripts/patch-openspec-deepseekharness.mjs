#!/usr/bin/env node
/**
 * patch-openspec-deepseekharness.mjs
 *
 * Adds DeepSeek Harness as a first-class "tool" (agent target) in the global
 * OpenSpec CLI (@fission-ai/openspec) so that:
 *
 *   - `openspec init --tools deepseekharness` (and the interactive tool
 *     picker) accepts DeepSeek Harness;
 *   - skills are generated into the project's shared `.agents/skills` root
 *     and the `.agents/skills/.openspec-target` ownership marker is
 *     recognised as `deepseekharness`;
 *   - generated skills reference sibling skills with `$openspec-<name>`
 *     (shell style, the same form DeepSeek Harness agents act on), instead
 *     of the `/openspec-<name>` slash-command form.
 *
 * The OpenSpec CLI has no user-configurable tool registry — AI_TOOLS is
 * hardcoded in the compiled dist — so this script patches the installed
 * package. It is idempotent and self-reverting; run it again after every
 * `npm i -g openspec` / `npm update -g openspec` upgrade.
 *
 * Usage:
 *   node scripts/patch-openspec-deepseekharness.mjs            # apply
 *   node scripts/patch-openspec-deepseekharness.mjs --check    # status only
 *   node scripts/patch-openspec-deepseekharness.mjs --revert   # undo
 *
 * Env overrides:
 *   OPENSPEC_PKG_DIR   absolute path to the openspec package (skip discovery)
 */

import { execSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

const PATCH_MARK = 'deepseekharness (patched by scripts/patch-openspec-deepseekharness.mjs)';

// Each patch: { file, description, applied(content), apply(content), revert(content) }
const PATCHES = [
  {
    file: 'dist/core/config.js',
    description: 'register DeepSeek Harness in AI_TOOLS (skillsDir .agents)',
    applied: (c) => c.includes("value: 'deepseekharness'"),
    apply: (c) => c.replace(
      "    { name: 'ZCode', value: 'zcode', available: true, successLabel: 'ZCode', skillsDir: '.zcode' },\n",
      "    { name: 'ZCode', value: 'zcode', available: true, successLabel: 'ZCode', skillsDir: '.zcode' },\n" +
        `    // ${PATCH_MARK}\n` +
        "    { name: 'DeepSeek Harness', value: 'deepseekharness', available: true, successLabel: 'DeepSeek Harness', skillsDir: '.agents', detectionPaths: ['.agents/skills'] },\n",
    ),
    revert: (c) => c
      .replace(`    // ${PATCH_MARK}\n`, '')
      .replace("    { name: 'DeepSeek Harness', value: 'deepseekharness', available: true, successLabel: 'DeepSeek Harness', skillsDir: '.agents', detectionPaths: ['.agents/skills'] },\n", ''),
  },
  {
    file: 'dist/core/command-surface.js',
    description: 'skills-invocable capability for deepseekharness (skills always generated)',
    applied: (c) => c.includes("toolId === 'codex' || toolId === 'deepseekharness'"),
    apply: (c) => c.replace(
      "    if (toolId === 'codex') {\n        return 'skills-invocable';\n    }\n",
      "    if (toolId === 'codex' || toolId === 'deepseekharness') {\n        return 'skills-invocable';\n    }\n",
    ),
    revert: (c) => c.replace(
      "    if (toolId === 'codex' || toolId === 'deepseekharness') {\n        return 'skills-invocable';\n    }\n",
      "    if (toolId === 'codex') {\n        return 'skills-invocable';\n    }\n",
    ),
  },
  {
    file: 'dist/utils/command-references.js',
    description: 'skill invocation prefix $ for deepseekharness ($openspec-<name>)',
    applied: (c) => c.includes('deepseekharness: \'$\'') || c.includes('deepseekharness: "$"'),
    // NOTE: replacement strings escape `$` as `$$` — in String.replace, `$'`
    // is the "text after the match" expansion, not a literal dollar sign.
    apply: (c) => c.replace(
      "    codex: '$',\n};",
      "    codex: '$$',\n    deepseekharness: '$$',\n};",
    ),
    revert: (c) => c.replace(
      "    codex: '$',\n    deepseekharness: '$',\n};",
      "    codex: '$$',\n};",
    ),
  },
];

function fail(message) {
  console.error(`[patch-openspec-deepseekharness] ${message}`);
  process.exit(1);
}

function resolvePackageDir() {
  if (process.env.OPENSPEC_PKG_DIR) {
    const dir = resolve(process.env.OPENSPEC_PKG_DIR);
    if (!existsSync(join(dir, 'package.json'))) fail(`OPENSPEC_PKG_DIR does not contain a package: ${dir}`);
    return dir;
  }
  try {
    const npmRoot = execSync('npm root -g', { encoding: 'utf8' }).trim();
    const candidate = join(npmRoot, '@fission-ai', 'openspec');
    if (existsSync(join(candidate, 'package.json'))) return candidate;
  } catch {
    /* fall through to command resolution */
  }
  try {
    const cmd = execSync(process.platform === 'win32' ? 'where openspec.cmd' : 'which openspec', { encoding: 'utf8' })
      .trim().split(/\r?\n/)[0];
    // Walk up from the launcher looking for node_modules/@fission-ai/openspec.
    let dir = dirname(cmd);
    for (let i = 0; i < 8 && dir !== dirname(dir); i++, dir = dirname(dir)) {
      const candidate = join(dir, 'node_modules', '@fission-ai', 'openspec');
      if (existsSync(join(candidate, 'package.json'))) return candidate;
    }
  } catch {
    /* fall through */
  }
  fail('could not locate the openspec package. Set OPENSPEC_PKG_DIR to its absolute path.');
}

function main() {
  const args = process.argv.slice(2);
  const mode = args.includes('--revert') ? 'revert' : args.includes('--check') ? 'check' : 'apply';
  const pkgDir = resolvePackageDir();

  let packageJson;
  try {
    packageJson = JSON.parse(readFileSync(join(pkgDir, 'package.json'), 'utf8'));
  } catch {
    fail(`cannot read ${join(pkgDir, 'package.json')}`);
  }
  if (packageJson.name !== '@fission-ai/openspec') {
    fail(`package at ${pkgDir} is "${packageJson.name}", expected @fission-ai/openspec`);
  }

  const results = [];
  for (const patch of PATCHES) {
    const abs = join(pkgDir, patch.file);
    if (!existsSync(abs)) {
      results.push({ patch, state: 'missing', error: `file not found: ${patch.file}` });
      continue;
    }
    const content = readFileSync(abs, 'utf8');
    const isApplied = patch.applied(content);
    if (mode === 'check') {
      results.push({ patch, state: isApplied ? 'applied' : 'not-applied' });
      continue;
    }
    if (mode === 'apply' && isApplied) {
      results.push({ patch, state: 'already-applied' });
      continue;
    }
    if (mode === 'revert' && !isApplied) {
      results.push({ patch, state: 'not-applied' });
      continue;
    }
    const next = mode === 'apply' ? patch.apply(content) : patch.revert(content);
    if (next === content) {
      results.push({ patch, state: 'noop', error: 'source did not match the expected snippet' });
      continue;
    }
    writeFileSync(abs, next, 'utf8');
    results.push({ patch, state: mode === 'apply' ? 'patched' : 'reverted' });
  }

  let failed = false;
  for (const { patch, state, error } of results) {
    const flag = ['patched', 'reverted', 'applied'].includes(state) ? 'OK' : state === 'already-applied' || state === 'not-applied' ? '--' : '!!';
    console.log(`[${flag}] ${patch.description} (${patch.file}): ${state}${error ? ` — ${error}` : ''}`);
    if (error) failed = true;
  }
  if (failed) fail(`one or more patches could not be ${mode === 'revert' ? 'reverted' : 'applied'} (openspec version ${packageJson.version}); the source layout may have changed in a newer release.`);
  console.log(`\nopenspec ${packageJson.version} @ ${pkgDir} — ${mode} done.`);

  if (mode === 'apply') {
    console.log('\nNext: mark the project skills root as DeepSeek Harness-owned and regenerate skills:');
    console.log('  openspec init --tools deepseekharness');
    console.log('(already done in this repo: .agents/skills/.openspec-target = deepseekharness)');
  }
  process.exit(0);
}

main();
