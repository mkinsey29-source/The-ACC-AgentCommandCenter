import { useState } from 'react';
import { Linking, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { uuid } from 'expo';
import { checkRuntime, Connection, normalizeEndpoint, Provider, providerNames, ProviderStatus, safeConnectionError, saveConnection } from './providers';

const setup = `pkg update\npkg install python git proot-distro\nproot-distro install debian\nproot-distro login debian`;

export function Connections({ connection, onChange }: { connection: Connection | null; onChange: (value: Connection | null) => void }) {
  const [url, setUrl] = useState(connection?.url || 'http://127.0.0.1:8765');
  const [token, setToken] = useState(connection?.token || '');
  const [allowApi, setAllowApi] = useState(connection?.allowApi || false);
  const [models, setModels] = useState(connection?.models || { claude: '', codex: '' });
  const [statuses, setStatuses] = useState<ProviderStatus[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function connect() {
    if (busy) return;
    setBusy(true); setMessage('Checking the runtime and both provider sign-ins…');
    let candidate: Connection | null = null;
    try {
      candidate = { id: connection?.id || uuid.v4(), url: normalizeEndpoint(url), token: token.trim(), allowApi, models };
      const changed = !connection || candidate.url !== connection.url || candidate.token !== connection.token || candidate.allowApi !== connection.allowApi || JSON.stringify(models) !== JSON.stringify(connection.models);
      if (changed) candidate.id = uuid.v4();
      const found = await checkRuntime(candidate);
      await saveConnection(candidate);
      setStatuses(found); onChange(candidate); setMessage('Runtime paired. A completed chat response will verify model access.');
    } catch (error) { setMessage(safeConnectionError(error, candidate)); }
    finally { setBusy(false); }
  }
  async function forget() {
    setBusy(true);
    try { await saveConnection(null); onChange(null); setToken(''); setStatuses([]); setMessage('ACC pairing removed. Provider sign-ins remain in their own runtimes.'); }
    catch { setMessage('Could not remove pairing from device storage.'); }
    finally { setBusy(false); }
  }
  return <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
    <Text style={s.text}>One runtime connects Claude Code and ChatGPT / Codex. Sign in to each provider in its own terminal. ACC keeps only its runtime pairing secret.</Text>
    {Platform.OS === 'web' && <Text style={s.notice}>Browser preview does not persist pairing and the runtime refuses browser-origin requests. Connect from the Android APK.</Text>}
    <Text style={s.heading}>Pair your runtime</Text>
    <Text style={s.text}>On this tablet: use the loopback address below. For a computer: use its HTTPS address. Changing the runtime or models starts a separate chat history.</Text>
    <TextInput accessibilityLabel="Runtime URL" editable={!busy} autoCapitalize="none" autoCorrect={false} value={url} onChangeText={setUrl} placeholder="http://127.0.0.1:8765" placeholderTextColor="#9aabc2" style={s.input} />
    <TextInput accessibilityLabel="Runtime pairing secret" editable={!busy} secureTextEntry autoCapitalize="none" autoCorrect={false} value={token} onChangeText={setToken} placeholder="Pairing secret from your runtime terminal" placeholderTextColor="#9aabc2" style={s.input} />
    {(['claude', 'codex'] as Provider[]).map(p => <View key={p} style={{ gap: 6 }}><Text style={s.text}>{providerNames[p]} model (blank uses provider default)</Text><TextInput accessibilityLabel={`${providerNames[p]} model`} editable={!busy} autoCapitalize="none" autoCorrect={false} value={models[p]} onChangeText={text => setModels(m => ({ ...m, [p]: text.trim() }))} placeholder="Provider default" placeholderTextColor="#9aabc2" style={s.input} /></View>)}
    <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: allowApi, disabled: busy }} disabled={busy} onPress={() => setAllowApi(!allowApi)} style={s.button}><Text style={s.notice}>{allowApi ? '☑' : '☐'} Allow separately billed API or unconfirmed billing</Text></Pressable>
    <Text style={s.text}>Off by default. Subscription sign-in does not guarantee quota or model eligibility. No automatic provider or billing fallback.</Text>
    <View style={s.row}><Pressable accessibilityRole="button" disabled={busy} onPress={connect} style={s.button}><Text style={s.blue}>{busy ? 'Checking…' : 'Pair and check both'}</Text></Pressable>{connection && <Pressable accessibilityRole="button" disabled={busy} onPress={forget} style={s.button}><Text style={s.text}>Forget pairing</Text></Pressable>}</View>
    {message ? <Text accessibilityRole="status" style={s.notice}>{message}</Text> : null}
    {statuses.map(p => <Text key={p.id} style={s.text}>{providerNames[p.id]} · {p.state.replaceAll('_', ' ')} · {p.billing} billing</Text>)}
    <Text style={s.heading}>First-time runtime setup</Text>
    <Text style={s.text}>Android-local setup is experimental: Termux runs a Linux environment for the official provider binaries. It is not embedded in this APK. Keep that terminal running. No paid always-on server is required.</Text>
    <Pressable accessibilityRole="button" onPress={() => Linking.openURL('https://github.com/termux/termux-app#installation').catch(() => setMessage('Could not open Termux installation instructions.'))} style={s.button}><Text style={s.blue}>Termux installation</Text></Pressable>
    <Text selectable style={s.code}>{setup}</Text>
    <Pressable accessibilityRole="button" onPress={() => Clipboard.setStringAsync(setup).then(() => setMessage('Initial Termux commands copied. Follow the runtime guide for the remaining steps.')).catch(() => setMessage('Could not copy. Select the commands above.'))} style={s.button}><Text style={s.blue}>Copy initial Termux commands</Text></Pressable>
    <Text style={s.text}>Then follow the runtime guide to install both tools and start ACC’s bridge. Complete “claude auth login” and “codex login” in that terminal. Enter the ACC pairing secret here—not a provider token.</Text>
    <Pressable accessibilityRole="button" onPress={() => Linking.openURL('https://github.com/mkinsey29-source/The-ACC-AgentCommandCenter/blob/feature/android-build-2-providers-20261019/mobile/runtime/README.md').catch(() => setMessage('Could not open the runtime guide.'))} style={s.button}><Text style={s.blue}>Open runtime setup guide</Text></Pressable>
  </ScrollView>;
}

const s = StyleSheet.create({
  content: { gap: 12, paddingBottom: 12 }, row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, text: { color: '#9aabc2', fontSize: 12, lineHeight: 19 },
  heading: { color: '#e5edf8', fontSize: 16, fontWeight: '700', marginTop: 8 }, notice: { color: '#f0d39a', fontSize: 12, lineHeight: 19 }, blue: { color: '#a7cff9', fontSize: 12, fontWeight: '600' },
  input: { color: '#e5edf8', backgroundColor: '#10151d', borderWidth: 1, borderColor: '#2d3a4e', borderRadius: 6, padding: 10, minHeight: 46 },
  button: { borderWidth: 1, borderColor: '#41658e', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 6, minHeight: 44, justifyContent: 'center' },
  code: { color: '#e5edf8', backgroundColor: '#10151d', padding: 10, fontFamily: 'monospace', fontSize: 11, lineHeight: 18 },
});
