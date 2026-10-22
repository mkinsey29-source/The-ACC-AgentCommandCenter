import { attachmentText, GitHubAttachment } from './github';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { uuid } from 'expo';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { cancelChatGPT, ChatGPTAccount, ChatGPTModel, chatGPTError, chatGPTModels, sendChatGPT } from './chatgpt';
import { Message } from './providers';

type History = { messages: Message[]; pending?: string; problem?: string };
export function ChatGPTThread({ account, project, openConnections, attachment, clearAttachment }: { account?: ChatGPTAccount; project: string; openConnections: () => void; attachment: GitHubAttachment | null; clearAttachment: () => void }) {
  const storageKey = `acc.chatgpt.v1:${project}:${account?.id || 'none'}`;
  const [history, setHistory] = useState<History>({ messages: [] });
  const [ready, setReady] = useState(false);
  const [draft, setDraft] = useState('');
  const [notice, setNotice] = useState('');
  const [models, setModels] = useState<ChatGPTModel[]>([]);
  const [model, setModel] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingModels, setLoadingModels] = useState(!!account?.signedIn);
  const [showModels, setShowModels] = useState(false);
  const alive = useRef(true), sending = useRef(false);
  const state = useRef<History>({ messages: [] });
  const writes = useRef(Promise.resolve());
  const transcript = useRef<React.ElementRef<typeof ScrollView>>(null);
  function save(value: History) {
    state.current = value;
    if (alive.current) setHistory(value);
    const raw = JSON.stringify(value);
    writes.current = writes.current.catch(() => {}).then(() => AsyncStorage.setItem(storageKey, raw));
    return writes.current;
  }
  async function loadModels() {
    if (!account?.signedIn || loadingModels) return;
    setLoadingModels(true);
    try {
      const value = await chatGPTModels();
      if (alive.current && value.accountId === account.id) { setModels(value.models); setModel(current => value.models.some(m => m.id === current) ? current : value.models[0].id); setNotice('Models loaded from your account.'); }
    } catch (error) { if (alive.current) setNotice(chatGPTError(error)); }
    finally { if (alive.current) setLoadingModels(false); }
  }
  useEffect(() => {
    alive.current = true;
    AsyncStorage.getItem(storageKey).then(async raw => {
      const value = raw ? JSON.parse(raw) as History : { messages: [] };
      if (!Array.isArray(value.messages) || value.messages.length > 40 || value.messages.some((m, i) => !m || m.role !== (i % 2 === 0 ? 'user' : 'assistant') || typeof m.text !== 'string' || m.text.length > 100000) || (value.pending && !/^[a-zA-Z0-9_-]{16,80}$/.test(value.pending))) throw new Error('Invalid saved chat');
      if (!alive.current) return;
      // No server-side stored response in this flow. Never resend after process death.
      if (value.pending) { await save({ messages: value.messages, problem: 'The previous request was interrupted. Its outcome is unknown and it has not been resent.' }); }
      else { state.current = value; setHistory(value); }
      if (alive.current) setReady(true);
    }).catch(() => { if (alive.current) setNotice('Saved chat could not be opened. Its data has been preserved.'); });
    return () => { alive.current = false; const id = state.current.pending; if (id && sending.current) void cancelChatGPT(id).catch(() => {}); };
    // Each keyed thread owns one project/account history.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    let current = true;
    if (!account?.signedIn) return;
    chatGPTModels().then(value => {
      if (current && value.accountId === account.id) { setModels(value.models); setModel(value.models[0].id); }
    }).catch(error => { if (current) setNotice(chatGPTError(error)); }).finally(() => { if (current) setLoadingModels(false); });
    return () => { current = false; };
  }, [account?.id, account?.signedIn]);
  async function send() {
    if (!ready || !account?.signedIn || !model || sending.current || state.current.pending || state.current.problem || !draft.trim()) return;
    const messages: Message[] = [...state.current.messages, { role: 'user', text: draft.trim() + (attachment ? attachmentText(attachment) : '') }];
    if (messages.length > 39 || messages.reduce((n, m) => n + m.text.length, 0) > 50000) { setNotice('Start a new conversation to keep context within the current limit.'); return; }
    const id = uuid.v4(); sending.current = true; setBusy(true); setNotice('Waiting for ChatGPT…');
    try {
      await save({ messages, pending: id });
      if (!alive.current) { await save({ messages, problem: 'Request was interrupted before sending.' }); return; }
      setDraft(''); clearAttachment();
      const reply = await sendChatGPT(id, account.id, model, messages);
      await save({ messages: [...messages, { role: 'assistant', text: reply }] });
      if (alive.current) setNotice('Response completed · saved on device');
    } catch (error) {
      const problem = chatGPTError(error);
      try { await save({ messages, problem }); } catch { if (alive.current) setNotice('Could not save the final request state. The request has not been resent.'); }
      if (alive.current) setNotice(problem);
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  }
  async function fresh() {
    if (sending.current || !ready) return;
    try { await save({ messages: [] }); setNotice('New conversation.'); } catch { setNotice('Could not save a new conversation.'); }
  }
  return <View style={s.grow}>
    <View style={s.context}><Text style={s.muted}>{account?.signedIn ? `${account.label} · Using ChatGPT plan` : 'Connect your ChatGPT account to begin'}</Text><Text style={s.muted}>Only this conversation and any file you explicitly attach are sent. Sentry is not attached automatically. GitHub access is read-only; no autonomous tools run.</Text>
      {account?.signedIn && <View style={s.row}><Pressable accessibilityRole="button" disabled={busy || loadingModels} onPress={() => setShowModels(!showModels)} style={s.button}><Text style={s.blue}>{loadingModels ? 'Loading models…' : models.find(m => m.id === model)?.name || 'Choose model'}</Text></Pressable><Pressable accessibilityRole="button" disabled={busy || loadingModels} onPress={loadModels} style={s.button}><Text style={s.muted}>Refresh models</Text></Pressable></View>}
      {showModels && <ScrollView style={{ maxHeight: 170 }}>{models.map(m => <Pressable accessibilityRole="radio" accessibilityState={{ checked: m.id === model }} key={m.id} disabled={busy} onPress={() => { setModel(m.id); setShowModels(false); }} style={s.button}><Text style={s.text}>{m.id === model ? '● ' : ''}{m.name}</Text></Pressable>)}</ScrollView>}
      {account?.signedIn && <Pressable accessibilityRole="link" onPress={() => Linking.openURL('https://chatgpt.com/settings/usage').catch(() => setNotice('Open ChatGPT Settings → Usage to manage limits.'))}><Text style={s.blue}>Manage usage</Text></Pressable>}
    </View>
    <ScrollView ref={transcript} style={s.grow} contentContainerStyle={s.messages} onContentSizeChange={() => transcript.current?.scrollToEnd({ animated: false })}>
      {!history.messages.length && <View style={s.empty}><Text style={s.heading}>ChatGPT in ACC</Text><Text style={s.muted}>{account?.signedIn ? 'Choose an available model and send a message. Your ChatGPT account allowance and limits apply.' : 'Tap Connect ChatGPT, sign in on the official OpenAI page, then return here. No terminal setup.'}</Text>{!account?.signedIn && <Pressable accessibilityRole="button" onPress={openConnections} style={s.button}><Text style={s.blue}>Connect ChatGPT</Text></Pressable>}</View>}
      {history.messages.map((m, i) => <View key={i} style={[s.message, m.role === 'user' && s.user]}><Text style={s.role}>{m.role === 'user' ? 'YOU' : 'CHATGPT'}</Text><Text selectable style={s.reply}>{m.text}</Text></View>)}
      {busy && <ActivityIndicator color="#a7cff9" />}
    </ScrollView>
    {notice || history.problem ? <Text role="status" accessibilityLiveRegion="polite" style={s.notice}>{notice || history.problem}</Text> : null}
    <View style={s.row}>{busy ? <Pressable accessibilityRole="button" onPress={() => history.pending && cancelChatGPT(history.pending).then(() => setNotice('Stop requested. Any partial response is not saved as completed.')).catch(() => setNotice('Stop could not be confirmed.'))} style={s.button}><Text style={s.text}>Stop request</Text></Pressable> : <Pressable accessibilityRole="button" disabled={!ready} onPress={fresh} style={s.button}><Text style={s.text}>New conversation</Text></Pressable>}</View>
    {attachment && <View style={s.row}><Text style={s.muted}>Next message: {attachment.repository}/{attachment.path} · {attachment.commit.slice(0, 8)}</Text><Pressable accessibilityRole="button" disabled={busy} onPress={clearAttachment} style={s.button}><Text style={s.blue}>Remove attachment</Text></Pressable></View>}
    <View style={s.composer}><TextInput accessibilityLabel="Chat message" multiline maxLength={10000} editable={ready && !busy && !history.problem} value={draft} onChangeText={setDraft} placeholder="Message ChatGPT…" placeholderTextColor="#9aabc2" style={s.input} /><Pressable accessibilityRole="button" disabled={!ready || !account?.signedIn || !model || busy || !!history.problem || !draft.trim()} onPress={send} style={s.button}><Text style={s.blue}>Send</Text></Pressable></View>
  </View>;
}
const s = StyleSheet.create({
  grow: { flex: 1, minHeight: 0 }, context: { paddingHorizontal: 14, paddingVertical: 8, gap: 6 }, muted: { color: '#9aabc2', fontSize: 12, lineHeight: 19 }, row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, paddingHorizontal: 10 },
  button: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 12, borderWidth: 1, borderColor: '#41658e', borderRadius: 6 }, blue: { color: '#a7cff9', fontSize: 12, fontWeight: '600' }, text: { color: '#e5edf8', fontSize: 12 },
  messages: { padding: 14, gap: 12 }, empty: { gap: 12, paddingVertical: 20 }, heading: { color: '#e5edf8', fontSize: 18, fontWeight: '700' }, message: { backgroundColor: '#19212d', borderRadius: 8, padding: 13, gap: 7 }, user: { backgroundColor: '#20334b' }, role: { color: '#a7cff9', fontSize: 10, fontWeight: '700' }, reply: { color: '#e5edf8', fontSize: 14, lineHeight: 22 },
  notice: { color: '#f0d39a', paddingHorizontal: 14, paddingVertical: 6, fontSize: 12, lineHeight: 19 }, composer: { flexDirection: 'row', gap: 8, padding: 12, borderTopWidth: 1, borderColor: '#2d3a4e', alignItems: 'flex-end' }, input: { flex: 1, minHeight: 46, maxHeight: 150, color: '#e5edf8', backgroundColor: '#19212d', borderRadius: 6, padding: 12, fontSize: 14 },
});
