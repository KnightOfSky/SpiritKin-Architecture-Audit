import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const tokenPath = path.join(repoRoot, "design", "tokens.json");
const schemaPath = path.join(repoRoot, "design", "tokens.schema.json");
const fontManifestPath = path.join(repoRoot, "design", "font-assets.json");
const iconManifestPath = path.join(repoRoot, "design", "icons.json");
const componentContractPath = path.join(repoRoot, "design", "component-states.json");
const jsonOutput = process.argv.includes("--json");
const tokensOnly = process.argv.includes("--tokens-only");
const fontsOnly = process.argv.includes("--fonts-only");
const errors = [];
const checks = [];

function readJson(filePath, label) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    errors.push(`${label}: ${error.message}`);
    return null;
  }
}

function linearChannel(value) {
  const channel = value / 255;
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

function rgbFromHex(hex) {
  return [1, 3, 5].map((index) => linearChannel(Number.parseInt(hex.slice(index, index + 2), 16)));
}

function luminance(hex) {
  const [red, green, blue] = rgbFromHex(hex);
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrastRatio(foreground, background) {
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

function oklchFromHex(hex) {
  const [red, green, blue] = rgbFromHex(hex);
  const l = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue;
  const m = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue;
  const s = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue;
  const lRoot = Math.cbrt(l);
  const mRoot = Math.cbrt(m);
  const sRoot = Math.cbrt(s);
  const lightness = 0.2104542553 * lRoot + 0.793617785 * mRoot - 0.0040720468 * sRoot;
  const axisA = 1.9779984951 * lRoot - 2.428592205 * mRoot + 0.4505937099 * sRoot;
  const axisB = 0.0259040371 * lRoot + 0.7827717662 * mRoot - 0.808675766 * sRoot;
  const chroma = Math.hypot(axisA, axisB);
  const hue = chroma < 0.00005 ? 0 : (Math.atan2(axisB, axisA) * 180 / Math.PI + 360) % 360;
  return { lightness: lightness * 100, chroma, hue };
}

function parsedOklch(value) {
  const match = /^oklch\(([0-9.]+)% ([0-9.]+) ([0-9.]+)\)$/.exec(value || "");
  return match ? { lightness: Number(match[1]), chroma: Number(match[2]), hue: Number(match[3]) } : null;
}

function hueDistance(first, second) {
  const distance = Math.abs(first - second) % 360;
  return Math.min(distance, 360 - distance);
}

function validateTheme(themeName, theme, requiredTokens) {
  if (!theme || typeof theme !== "object") {
    errors.push(`themes.${themeName} is missing`);
    return;
  }
  for (const tokenName of requiredTokens) {
    const token = theme[tokenName];
    if (!token) {
      errors.push(`themes.${themeName}.${tokenName} is missing`);
      continue;
    }
    if (!/^#[0-9A-F]{6}$/.test(token.hex || "")) {
      errors.push(`themes.${themeName}.${tokenName}.hex is invalid`);
      continue;
    }
    const stored = parsedOklch(token.oklch);
    if (!stored) {
      errors.push(`themes.${themeName}.${tokenName}.oklch is invalid`);
      continue;
    }
    const calculated = oklchFromHex(token.hex);
    if (
      Math.abs(stored.lightness - calculated.lightness) > 0.08 ||
      Math.abs(stored.chroma - calculated.chroma) > 0.0008 ||
      (calculated.chroma >= 0.00005 && hueDistance(stored.hue, calculated.hue) > 0.25)
    ) {
      errors.push(`themes.${themeName}.${tokenName}.oklch does not match ${token.hex}`);
    }
  }
}

function validatePlatformMarkers() {
  const targets = [
    ["frontend/styles/fantasy-tokens.css", /tokens\.json v4/i],
    ["desktop/SpiritKinDesktop/Resources/Themes/Fantasy.Light.xaml", /tokens\.json v4/i],
    ["desktop/SpiritKinDesktop/Resources/Themes/Fantasy.Dark.xaml", /tokens\.json v4/i],
    ["ios/SpiritKinTerminal/Sources/Support/Theme.swift", /tokens\.json v4/i],
    ["mobile-link-bridge/res/values/colors.xml", /tokens\.json v4/i],
    ["mobile-link-bridge/res/values-night/colors.xml", /tokens\.json v4/i]
  ];
  for (const [relativePath, marker] of targets) {
    const filePath = path.join(repoRoot, relativePath);
    if (!fs.existsSync(filePath)) {
      errors.push(`${relativePath} is missing`);
      continue;
    }
    if (!marker.test(fs.readFileSync(filePath, "utf8"))) {
      errors.push(`${relativePath} is not marked as tokens.json v4`);
    }
  }
}

function expectFileContains(relativePath, expected, label = relativePath) {
  const filePath = path.join(repoRoot, relativePath);
  if (!fs.existsSync(filePath)) {
    errors.push(`${relativePath} is missing`);
    return;
  }
  const content = fs.readFileSync(filePath, "utf8");
  for (const value of expected) {
    if (!content.includes(value)) errors.push(`${label} is missing ${value}`);
  }
}

function validatePlatformValues(data) {
  const mapping = data.mapping || {};
  for (const [themeName, fileName] of [["light", "Fantasy.Light.xaml"], ["dark", "Fantasy.Dark.xaml"]]) {
    const theme = data.themes[themeName];
    const expected = Object.entries(mapping).map(([key, token]) => `x:Key="${key}" Color="${theme[token].hex}"`);
    expectFileContains(`desktop/SpiritKinDesktop/Resources/Themes/${fileName}`, expected, `WPF ${themeName}`);
  }

  const css = fs.readFileSync(path.join(repoRoot, "frontend/styles/fantasy-tokens.css"), "utf8").toLowerCase();
  for (const [selector, themeName] of [[":root", "light"], ["[data-theme=\"dark\"]", "dark"]]) {
    const start = css.indexOf(`${selector} {`);
    const end = css.indexOf("}", start);
    const block = start >= 0 && end > start ? css.slice(start, end) : "";
    for (const [variable, token] of Object.entries({
      "--fx-primary": "accent", "--fx-copper": "copper", "--fx-canvas": "canvas",
      "--fx-surface": "surface", "--fx-surface-2": "surface-2", "--fx-surface-3": "surface-3",
      "--fx-text": "text", "--fx-text-muted": "muted", "--fx-text-faint": "faint",
      "--fx-line": "line", "--fx-line-strong": "control-border", "--fx-focus": "focus-ring"
    })) {
      const expected = `${variable}: ${data.themes[themeName][token].hex.toLowerCase()}`;
      if (!block.includes(expected)) errors.push(`Web ${themeName} is missing ${expected}`);
    }
  }

  const swiftPairs = Object.entries({
    primary: "accent", secondary: "copper", success: "success-fg", warning: "warning-fg",
    danger: "danger-fg", info: "info-fg", text: "text", muted: "muted", faint: "faint",
    surface: "surface", canvas: "canvas", surface2: "surface-2", surface3: "surface-3",
    line: "line", lineStrong: "control-border"
  }).map(([name, token]) => {
    const light = data.themes.light[token].hex.slice(1);
    const dark = data.themes.dark[token].hex.slice(1);
    return `static let ${name} = Color(light: 0x${light}, dark: 0x${dark})`;
  });
  expectFileContains("ios/SpiritKinTerminal/Sources/Support/Theme.swift", swiftPairs, "iOS theme");

  const androidNames = {
    canvas: "fantasy_canvas", surface: "fantasy_surface", "surface-2": "fantasy_surface_2",
    "surface-3": "fantasy_surface_3", line: "fantasy_line", "control-border": "fantasy_control_border",
    text: "fantasy_text", muted: "fantasy_muted", faint: "fantasy_faint", accent: "fantasy_accent",
    "accent-2": "fantasy_accent_2", copper: "fantasy_copper", "success-fg": "fantasy_success_fg",
    "success-bg": "fantasy_success_bg", "warning-fg": "fantasy_warning_fg", "warning-bg": "fantasy_warning_bg",
    "danger-fg": "fantasy_danger_fg", "danger-bg": "fantasy_danger_bg", "info-fg": "fantasy_info_fg",
    "info-bg": "fantasy_info_bg"
  };
  for (const [themeName, relativePath] of [["light", "mobile-link-bridge/res/values/colors.xml"], ["dark", "mobile-link-bridge/res/values-night/colors.xml"]]) {
    const expected = Object.entries(androidNames).map(([token, name]) => `<color name="${name}">${data.themes[themeName][token].hex}</color>`);
    expectFileContains(relativePath, expected, `Android ${themeName}`);
  }
}

function validateIconRegistry() {
  const manifest = readJson(iconManifestPath, "icons.json");
  if (!manifest) return;
  if (manifest.$schema !== "./icons.schema.json" || manifest.version !== 1) errors.push("icons.json schema/version is invalid");
  const requiredPlatforms = ["wpf", "web", "ios", "android"];
  for (const [semanticId, icon] of Object.entries(manifest.icons || {})) {
    if (!/^(action|navigation|entity|state)\.[a-z0-9-]+$/.test(semanticId)) errors.push(`icons.json invalid semantic id ${semanticId}`);
    if (!icon.label_zh || !icon.category) errors.push(`icons.json ${semanticId} is missing label/category`);
    for (const platform of requiredPlatforms) if (!icon[platform]?.name && !icon[platform]?.glyph) errors.push(`icons.json ${semanticId} is missing ${platform} mapping`);
    const webName = icon.web?.name;
    if (webName && !fs.existsSync(path.join(repoRoot, "frontend/icons/lucide", `${webName}.svg`))) errors.push(`Lucide asset ${webName}.svg is missing`);
  }
  expectFileContains(manifest.web_library?.license_path || "frontend/icons/lucide/LICENSE", ["ISC License"], "Lucide license");
}

function validateComponentContract() {
  const contract = readJson(componentContractPath, "component-states.json");
  if (!contract) return;
  const required = {
    button: ["default", "hover", "pressed", "focus", "disabled", "loading"],
    input: ["default", "hover", "focus", "invalid", "disabled", "read-only"],
    "nav-list": ["default", "hover", "selected", "keyboard-focus", "unavailable"],
    "card-panel": ["default", "loading", "empty", "partial-error", "offline"],
    status: ["info", "success", "warning", "danger", "unknown"],
    "dialog-sheet": ["default", "destructive", "busy", "error"]
  };
  if (contract.$schema !== "./component-states.schema.json" || contract.token_authority !== "design/tokens.json v4") errors.push("component-states.json schema/authority is invalid");
  for (const [component, states] of Object.entries(required)) {
    const actual = new Set(contract.components?.[component]?.states || []);
    for (const state of states) if (!actual.has(state)) errors.push(`component ${component} is missing state ${state}`);
    if (!(contract.components?.[component]?.requirements || []).length) errors.push(`component ${component} has no requirements`);
  }
  for (const platform of ["wpf", "web", "ios", "android"]) if (!(contract.platform_acceptance?.[platform] || []).length) errors.push(`component acceptance is missing ${platform}`);
}

function sha256(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function validateFontAssets() {
  const manifest = readJson(fontManifestPath, "font-assets.json");
  if (!manifest) return;

  for (const [fontId, font] of Object.entries(manifest.fonts || {})) {
    if (!font.repository || !/^[0-9a-f]{40}$/.test(font.revision || "")) {
      errors.push(`font-assets.json ${fontId} must pin a repository and 40-character revision`);
    }
    for (const asset of font.assets || []) {
      const filePath = path.join(repoRoot, asset.path || "");
      if (!fs.existsSync(filePath)) {
        errors.push(`${asset.path || fontId} is missing`);
        continue;
      }
      const signature = fs.readFileSync(filePath).subarray(0, 4);
      const validFormat = asset.format === "ttf"
        ? signature.equals(Buffer.from([0x00, 0x01, 0x00, 0x00]))
        : asset.format === "woff2" && signature.toString("ascii") === "wOF2";
      if (!validFormat) errors.push(`${asset.path} is not a valid ${asset.format} file`);
      if (sha256(filePath) !== asset.sha256) errors.push(`${asset.path} SHA-256 does not match font-assets.json`);
    }
    for (const relativePath of font.license_paths || []) {
      const filePath = path.join(repoRoot, relativePath);
      if (!fs.existsSync(filePath)) errors.push(`${relativePath} is missing`);
      else if (sha256(filePath) !== font.license_sha256) errors.push(`${relativePath} SHA-256 does not match font-assets.json`);
    }
  }

  const cssPath = path.join(repoRoot, "frontend", "styles", "fantasy-tokens.css");
  const css = fs.readFileSync(cssPath, "utf8");
  for (const expected of [
    'font-family: "Orbitron"',
    'url("../fonts/orbitron-latin.woff2") format("woff2")',
    'font-family: "JetBrains Mono"',
    'url("../fonts/jetbrains-mono.woff2") format("woff2")',
    '--font-brand-latin: Orbitron',
    '--font-mono: "JetBrains Mono"'
  ]) {
    if (!css.includes(expected)) errors.push(`frontend/styles/fantasy-tokens.css is missing ${expected}`);
  }

  const projectPath = path.join(repoRoot, "desktop", "SpiritKinDesktop", "SpiritKinDesktop.csproj");
  const project = fs.readFileSync(projectPath, "utf8");
  for (const expected of ["Assets\\Fonts\\Orbitron-Variable.ttf", "Assets\\Fonts\\JetBrainsMono-Variable.ttf"]) {
    if (!project.includes(expected)) errors.push(`desktop/SpiritKinDesktop/SpiritKinDesktop.csproj is missing ${expected}`);
  }

  const textExtensions = new Set([".css", ".html", ".js", ".xaml", ".cs", ".csproj"]);
  for (const relativeRoot of ["frontend", "desktop/SpiritKinDesktop"]) {
    const pending = [path.join(repoRoot, relativeRoot)];
    while (pending.length) {
      const current = pending.pop();
      for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
        const entryPath = path.join(current, entry.name);
        const relativePath = path.relative(repoRoot, entryPath).replaceAll(path.sep, "/");
        if (entry.isDirectory()) pending.push(entryPath);
        else {
          if (/new[-_ ]?rocker/i.test(entry.name)) errors.push(`${relativePath} is a forbidden New Rocker production asset`);
          if (textExtensions.has(path.extname(entry.name).toLowerCase()) && /new[ -]?rocker/i.test(fs.readFileSync(entryPath, "utf8"))) {
            errors.push(`${relativePath} contains a forbidden New Rocker production reference`);
          }
        }
      }
    }
  }
}

const schema = readJson(schemaPath, "tokens.schema.json");
const data = readJson(tokenPath, "tokens.json");
if (fontsOnly) {
  validateFontAssets();
} else if (!schema || !data) {
  process.exitCode = 1;
} else {
  if (data.$schema !== "./tokens.schema.json") errors.push("tokens.json $schema must be ./tokens.schema.json");
  if (data.meta?.version !== 4) errors.push("tokens.json meta.version must be 4");
  const requiredTokens = schema.$defs?.theme?.required || [];
  validateTheme("dark", data.themes?.dark, requiredTokens);
  validateTheme("light", data.themes?.light, requiredTokens);

  for (const themeName of ["dark", "light"]) {
    const theme = data.themes?.[themeName];
    if (!theme) continue;
    for (const check of data.contrast_checks || []) {
      const foreground = theme[check.foreground]?.hex;
      const background = theme[check.background]?.hex;
      if (!foreground || !background) {
        errors.push(`${themeName} contrast check references missing token ${check.foreground}/${check.background}`);
        continue;
      }
      const ratio = contrastRatio(foreground, background);
      checks.push({ theme: themeName, pair: `${check.foreground}/${check.background}`, ratio, minimum: check.minimum });
      if (ratio + 1e-9 < check.minimum) {
        errors.push(`${themeName} ${check.foreground}/${check.background} is ${ratio.toFixed(2)}:1, expected ${check.minimum}:1`);
      }
    }
  }

  if (!tokensOnly) {
    validatePlatformMarkers();
    validatePlatformValues(data);
    validateFontAssets();
    validateIconRegistry();
    validateComponentContract();
  }
}

const result = { ok: errors.length === 0, token_version: data?.meta?.version || null, checks, errors };
if (jsonOutput) {
  console.log(JSON.stringify(result, null, 2));
} else {
  for (const check of checks) {
    console.log(`PASS ${check.theme} ${check.pair}: ${check.ratio.toFixed(2)} >= ${check.minimum}`);
  }
  for (const error of errors) console.error(`ERROR ${error}`);
  console.log(result.ok ? "Design token validation passed." : "Design token validation failed.");
}
if (!result.ok) process.exitCode = 1;
