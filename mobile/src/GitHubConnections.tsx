import { useRef, useState } from 'react';
import { Linking, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { connectGitHub, forgetGitHub, githubError } from './github';

export function GitHubConnections({ connected, refresh }: { connected: boolean; refresh: () => Promise<void> }) {
  const [token, setToken] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const working = useRef(false);
  async function connect() {
    if (working.current) return;
    working.current = true; setBusy(true);
    try { await connectGitHub(token); setToken(''); await refresh(); setNotice('GitHub account verified. Choose a repository in the GitHub tab. File access is checked when you open it.'); }
    catch (e) { setNotice(githubError(e)); }
    finally { working.current = false; setBusy(false); }
  }
  async function forget() {
    if (working.current) return;
    working.current = true; setBusy(true);
    try { await forgetGitHub(); setToken(''); await refresh(); setNotice('Token removed from ACC. Revoke it in GitHub to remove its access there. Files already sent remain in conversation history.'); }
    catch { setNotice('Could not remove the saved token. Try again.'); }
    finally { working.current = false; setBusy(false); }
  }
  return <View style={s.card}>
    <Text style={s.heading}>GitHub</Text>
    <Text style={s.text}>{connected ? 'Credential saved · repository access checked on request' : 'Public repositories work without sign-in. For private repositories, connect a selected-repository token.'}</Text>
    <Text style={s.text}>1. Create a fine-grained token on GitHub. Choose only the repositories you need and Contents: Read-only. Set an expiry.</Text>
    <Pressable accessibilityRole="button" disabled={busy} onPress={() => Linking.openURL('https://github.com/settings/personal-access-tokens/new?name=ACC%20Android&description=Read%20selected%20repositories%20in%20ACC&contents=read&expires_in=30').catch(() => setNotice('Could not open GitHub. Open GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens.'))} style={s.button}><Text style={s.blue}>Create GitHub token in browser</Text></Pressable>
    <Text style={s.text}>2. Paste the token below, then Connect GitHub. Enter it only here, never in a chat. ACC uses read requests; it cannot push or merge code.</Text>
    {Platform.OS === 'web' && <Text style={s.text}>Preview keeps the token only in memory. Android saves it encrypted on this device.</Text>}
    <TextInput accessibilityLabel="GitHub access token" secureTextEntry autoCapitalize="none" autoCorrect={false} editable={!busy} value={token} onChangeText={setToken} maxLength={270} placeholder="Fine-grained GitHub token" placeholderTextColor="#9aabc2" style={s.input} />
    <View style={s.row}><Pressable accessibilityRole="button" disabled={busy || !token.trim()} onPress={connect} style={s.button}><Text style={s.blue}>{busy ? 'Working…' : 'Connect GitHub'}</Text></Pressable>{connected && <Pressable accessibilityRole="button" disabled={busy} onPress={forget} style={s.button}><Text style={s.blue}>Disconnect GitHub</Text></Pressable>}</View>
    {notice ? <Text role="status" accessibilityLiveRegion="polite" style={s.text}>{notice}</Text> : null}
  </View>;
}
const s = StyleSheet.create({
  card: { gap: 9, borderWidth: 1, borderColor: '#41658e', padding: 14, borderRadius: 8 }, heading: { color: '#e5edf8', fontSize: 16, fontWeight: '700' }, text: { color: '#9aabc2', fontSize: 12, lineHeight: 19 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, button: { borderWidth: 1, borderColor: '#41658e', paddingHorizontal: 12, minHeight: 44, borderRadius: 6, justifyContent: 'center' }, blue: { color: '#a7cff9', fontSize: 12, fontWeight: '600' }, input: { color: '#e5edf8', borderWidth: 1, borderColor: '#2d3a4e', borderRadius: 6, padding: 10, minHeight: 46 },
});
