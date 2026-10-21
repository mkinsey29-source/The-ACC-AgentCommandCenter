// Check actual Android role enums, rather than React Native's permissive TS strings.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const delegate = fs.readFileSync(path.join(root, 'node_modules/react-native/ReactAndroid/src/main/java/com/facebook/react/uimanager/ReactAccessibilityDelegate.kt'), 'utf8');
function enumValues(name) {
  const match = delegate.match(new RegExp(`public enum class ${name} \\{([\\s\\S]*?);`));
  assert(match, `Missing native ${name} enum`);
  return new Set(match[1].replace(/\/\/[^\n]*/g, '').split(',').map(value => value.trim().toLowerCase()).filter(Boolean));
}
const enums = { accessibilityRole: enumValues('AccessibilityRole'), role: enumValues('Role') };
const failures = [];
let checked = 0;
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) { walk(file); continue; }
    if (!/\.tsx$/.test(file)) continue;
    const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    function visit(node) {
      if (ts.isJsxAttribute(node)) {
        const name = node.name.getText(source);
        if (enums[name] && node.initializer && ts.isStringLiteral(node.initializer)) {
          checked++;
          const value = node.initializer.text;
          if (!enums[name].has(value.toLowerCase())) failures.push(`${path.relative(root, file)}:${source.getLineAndCharacterOfPosition(node.pos).line + 1}: Android rejects ${name}="${value}"`);
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
}
walk(path.join(root, 'src'));
assert(checked > 20, 'No meaningful native prop coverage');
assert.deepEqual(failures, [], failures.join('\n'));
console.log(`Native Android role contract: ${checked} JSX roles accepted by the installed React Native enums.`);
