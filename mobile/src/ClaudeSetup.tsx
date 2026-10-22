import { useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';

export function ClaudeSetup({ repository }: { repository?: string }) {
  const [notice, setNotice] = useState('');
  return <View style={s.card}>
    <Text style={s.heading}>Claude Code in the browser</Text>
    <Text style={s.text}>Use your Claude subscription on the official Claude Code page. No terminal or companion computer is needed. Your plan and organization must support cloud sessions.</Text>
    <Text style={s.text}>1. Open Claude Code and sign in on Claude’s page.</Text>
    <Text style={s.text}>2. Follow Claude’s GitHub setup and select the repositories you want Claude to use. This is separate from ACC’s GitHub connection.</Text>
    <Text style={s.text}>3. Choose your repository and start a task there. Replies, approvals and changes stay in Claude’s browser session; ACC cannot read or monitor that session.</Text>
    {repository && <><Text selectable style={s.blue}>Selected in ACC: {repository}</Text><Pressable accessibilityRole="button" onPress={() => Clipboard.setStringAsync(`https://github.com/${repository}`).then(() => setNotice('Repository URL copied. Select the same repository in Claude Code.')).catch(() => setNotice('Could not copy. Select the repository name above.'))} style={s.button}><Text style={s.blue}>Copy repository URL</Text></Pressable></>}
    <Pressable accessibilityRole="button" onPress={() => Linking.openURL('https://claude.ai/code').then(() => setNotice('Opened Claude Code. Finish setup in your browser; ACC has not verified a Claude sign-in or reply.')).catch(() => setNotice('Could not open Claude Code. Choose a default browser in Android settings.'))} style={s.button}><Text style={s.blue}>Open Claude Code in browser</Text></Pressable>
    <Text style={s.text}>Browser handoff · Claude is not signed into ACC. Sentry and ACC conversations are not sent. ACC tasks remain manual planning items.</Text>
    {notice ? <Text role="status" accessibilityLiveRegion="polite" style={s.text}>{notice}</Text> : null}
  </View>;
}
const s = StyleSheet.create({ card: { gap: 10 }, heading: { color: '#e5edf8', fontSize: 16, fontWeight: '700' }, text: { color: '#9aabc2', fontSize: 12, lineHeight: 19 }, blue: { color: '#a7cff9', fontSize: 12, fontWeight: '600' }, button: { borderWidth: 1, borderColor: '#41658e', borderRadius: 6, minHeight: 44, justifyContent: 'center', paddingHorizontal: 12 } });
