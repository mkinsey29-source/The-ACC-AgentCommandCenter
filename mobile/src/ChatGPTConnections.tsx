import { useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { cancelChatGPTSignIn, ChatGPTStatus, chatGPTError, selectChatGPTAccount, signInChatGPT, signOutChatGPT, supportsChatGPT } from './chatgpt';

export function ChatGPTConnections({ status, refresh }: { status: ChatGPTStatus; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function act(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true); setMessage('');
    try { const result = await action(); if (typeof result === 'string') setMessage(result); await refresh(); }
    catch (error) { setMessage(chatGPTError(error)); }
    finally { setBusy(false); }
  }
  const active = status.accounts.find(a => a.id === status.active);
  const resumable = active || status.accounts.find(a => !a.signedIn);
  return <View style={s.card}>
    <Text style={s.heading}>ChatGPT</Text>
    <Text style={s.text}>Sign in with your existing account in the official OpenAI browser page, then return to ACC. No Termux, terminal commands or API key needed for this connection.</Text>
    <Text style={s.notice}>Local preview connection · account eligibility and plan limits apply. This creates ACC conversations; it does not import your ChatGPT chats or memory. Paid-app access is still being evaluated.</Text>
    {!supportsChatGPT && <Text style={s.notice}>Direct sign-in is available in the Android build 3 APK. This browser preview cannot sign in.</Text>}
    {status.accounts.map(a => <View key={a.id} style={{ gap: 6 }}><Text style={s.text}>{a.id === status.active ? '● ' : ''}{a.label} · {a.signedIn ? 'connected' : 'signed out'}</Text>{a.id !== status.active && <Pressable accessibilityRole="button" disabled={busy || status.signingIn} onPress={() => act(() => selectChatGPTAccount(a.id))} style={s.button}><Text style={s.blue}>Use this account</Text></Pressable>}</View>)}
    <View style={s.row}>
      <Pressable accessibilityRole="button" disabled={!supportsChatGPT || busy || status.signingIn} onPress={() => act(() => signInChatGPT(resumable?.id || null))} style={s.button}><Text style={s.blue}>{status.signingIn ? 'Waiting for browser sign-in…' : 'Continue with ChatGPT'}</Text></Pressable>
      {status.accounts.length > 0 && <Pressable accessibilityRole="button" disabled={busy || status.signingIn} onPress={() => act(() => signInChatGPT(null))} style={s.button}><Text style={s.blue}>Add another account</Text></Pressable>}
      {status.signingIn && <Pressable accessibilityRole="button" disabled={busy} onPress={() => act(cancelChatGPTSignIn)} style={s.button}><Text style={s.text}>Cancel sign-in</Text></Pressable>}
      {active?.signedIn && <Pressable accessibilityRole="button" disabled={busy || status.signingIn} onPress={() => act(() => signOutChatGPT(active.id))} style={s.button}><Text style={s.text}>Sign out</Text></Pressable>}
    </View>
    {message || status.message ? <Text accessibilityRole="status" style={s.notice}>{message || status.message}</Text> : null}
    <Pressable accessibilityRole="link" onPress={() => Linking.openURL('https://chatgpt.com/settings/usage').catch(() => setMessage('Could not open ChatGPT settings. Open ChatGPT and choose Settings → Usage.'))}><Text style={s.blue}>Manage account usage and app access in ChatGPT</Text></Pressable>
  </View>;
}
const s = StyleSheet.create({
  card: { gap: 12, padding: 14, borderWidth: 1, borderColor: '#41658e', backgroundColor: '#10151d', borderRadius: 8 }, heading: { color: '#e5edf8', fontSize: 18, fontWeight: '700' },
  text: { color: '#9aabc2', fontSize: 12, lineHeight: 19 }, notice: { color: '#f0d39a', fontSize: 12, lineHeight: 19 }, row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  button: { borderWidth: 1, borderColor: '#41658e', borderRadius: 6, paddingHorizontal: 12, paddingVertical: 10, minHeight: 44, justifyContent: 'center' }, blue: { color: '#a7cff9', fontSize: 12, fontWeight: '600' },
});
