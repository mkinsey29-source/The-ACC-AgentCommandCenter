import AsyncStorage from '@react-native-async-storage/async-storage';
import { uuid } from 'expo';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { Connection, Job, Message, Provider, providerNames, runtimeRequest, safeConnectionError, validateJob } from './providers';

type History = { messages: Message[]; pending?: string; problem?: string };
const blank: History = { messages: [] };

export function Chat({ connection, project, openConnections }: { connection: Connection | null; project: string; openConnections: () => void }) {
  const [provider, setProvider] = useState<Provider>('claude');
  return <View style={s.grow}>
    <View style={s.header}><View style={s.row}>{(['claude', 'codex'] as Provider[]).map(p => <Pressable key={p} accessibilityRole="tab" accessibilityState={{ selected: p === provider }} onPress={() => setProvider(p)} style={[s.button, provider === p && s.active]}><Text style={s.text}>{providerNames[p]}</Text></Pressable>)}</View><Pressable accessibilityRole="button" onPress={openConnections} style={s.button}><Text style={s.blue}>Connections</Text></Pressable></View>
    <Thread key={`${project}:${connection?.id || 'none'}:${provider}`} connection={connection} project={project} provider={provider} openConnections={openConnections} />
  </View>;
}

function Thread({ connection, project, provider, openConnections }: { connection: Connection | null; project: string; provider: Provider; openConnections: () => void }) {
  const storageKey = `acc.chat.v1:${project}:${connection?.id || 'none'}:${provider}`;
  const [history, setHistory] = useState<History>(blank);
  const [ready, setReady] = useState(false);
  const [draft, setDraft] = useState('');
  const [notice, setNotice] = useState('');
  const [working, setWorking] = useState(false);
  const alive = useRef(true);
  const state = useRef<History>(blank);
  const queue = useRef(Promise.resolve());
  const sendLock = useRef(false);
  const transcript = useRef<React.ElementRef<typeof ScrollView>>(null);

  function update(value: History) {
    state.current = value;
    if (alive.current) setHistory(value);
    const raw = JSON.stringify(value);
    queue.current = queue.current.catch(() => {}).then(() => AsyncStorage.setItem(storageKey, raw));
    return queue.current;
  }

  async function watch(id: string) {
    if (!connection || sendLock.current) return;
    sendLock.current = true; setWorking(true); setNotice('Waiting for the provider…');
    const deadline = Date.now() + 180_000;
    try {
      while (alive.current) {
        const job = validateJob(await runtimeRequest(connection, `/v1/jobs/${id}`), id, provider);
        if (!alive.current) return;
        if (job.state !== 'running') { await finish(job); return; }
        if (Date.now() > deadline) throw new Error('Still waiting. Use Check response to reconnect to this request; it will not be resent.');
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    } catch (error) {
      if (alive.current) setNotice(safeConnectionError(error, connection) + ' The request has not been resent.');
    } finally { sendLock.current = false; if (alive.current) setWorking(false); }
  }

  async function finish(job: Job) {
    const current = state.current;
    if (current.pending !== job.id) return;
    const messages = job.state === 'completed' ? [...current.messages, { role: 'assistant' as const, text: job.reply }] : current.messages;
    await update({ messages, problem: job.state === 'completed' ? undefined : job.error || 'Request cancelled. Start a new conversation before sending again.' });
    if (alive.current) setNotice(job.state === 'completed' ? 'Response completed · saved on device' : job.error || 'Request cancelled.');
  }

  useEffect(() => {
    alive.current = true;
    AsyncStorage.getItem(storageKey).then(raw => {
      if (!alive.current) return;
      const value = raw ? JSON.parse(raw) as History : blank;
      if (!Array.isArray(value.messages) || value.messages.length > 40 || value.messages.some((m, i) => !m || m.role !== (i % 2 === 0 ? 'user' : 'assistant') || typeof m.text !== 'string' || m.text.length > 100_000) ||
          (value.pending && !/^[a-zA-Z0-9_-]{16,80}$/.test(value.pending))) throw new Error('Invalid history');
      state.current = value; setHistory(value); setReady(true);
      if (value.pending) void watch(value.pending);
    }).catch(() => { if (alive.current) setNotice('Chat history could not be opened. Its saved data has been preserved.'); });
    return () => { alive.current = false; };
    // A Thread is keyed by project, connection and provider; each owns its writes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function send() {
    if (!connection || sendLock.current || !ready || state.current.pending || state.current.problem || !draft.trim()) return;
    if (history.messages.length >= 38) { setNotice('Start a new conversation to keep context within the runtime limit.'); return; }
    const messages: Message[] = [...state.current.messages, { role: 'user', text: draft.trim() }];
    if (messages.reduce((n, m) => n + m.text.length, 0) > 50_000) { setNotice('Conversation is too long. Start a new one.'); return; }
    const id = uuid.v4();
    sendLock.current = true; setWorking(true); setNotice('Sending to ' + providerNames[provider] + '…');
    try {
      // Persist intent before network dispatch. Recovery only polls this ID; never auto-replays a POST.
      await update({ messages, pending: id });
      if (!alive.current) return;
      setDraft('');
      const job = validateJob(await runtimeRequest(connection, '/v1/chat', { id, provider, model: connection.models[provider], messages, allow_api: connection.allowApi }), id, provider);
      if (!alive.current) return;
      if (job.state !== 'running') await finish(job);
      else { sendLock.current = false; await watch(id); }
    } catch (error) {
      if (alive.current) setNotice(safeConnectionError(error, connection) + ' Use Check response before starting another request.');
    } finally { sendLock.current = false; if (alive.current) setWorking(false); }
  }

  async function cancel() {
    const id = state.current.pending;
    if (!connection || !id) return;
    try {
      const job = validateJob(await runtimeRequest(connection, `/v1/jobs/${id}/cancel`, {}), id, provider);
      if (alive.current) setNotice(job.state === 'running' ? 'Cancellation requested. Waiting for the runtime to confirm.' : 'Request has ended. Check response to save its final state.');
      if (!sendLock.current) await watch(id);
    } catch (error) { if (alive.current) setNotice(safeConnectionError(error, connection) + ' Cancellation is not confirmed.'); }
  }

  async function fresh() {
    if (state.current.pending || working) return;
    try { await update(blank); setNotice('New conversation.'); } catch { setNotice('Could not save a new conversation.'); }
  }

  async function keepInterrupted() {
    if (working || !state.current.pending) return;
    try {
      await update({ messages: state.current.messages, problem: 'Outcome unknown. The provider may still be running. Check its terminal; this does not stop it.' });
      setNotice('Saved as interrupted. Check the runtime terminal before starting a new conversation.');
    } catch { setNotice('Could not save the interrupted state.'); }
  }

  return <View style={s.grow}>
    <View style={s.context}><Text style={s.muted}>{connection ? `Paired runtime · ${connection.models[provider] || 'provider default'} · text chat` : 'No runtime paired'}</Text><Text style={s.muted}>ACC sends this conversation. Sentry notes and project files are not attached automatically.</Text></View>
    <ScrollView ref={transcript} style={s.grow} contentContainerStyle={s.messages} onContentSizeChange={() => transcript.current?.scrollToEnd({ animated: false })}>
      {!history.messages.length && <View style={s.empty}><Text style={s.heading}>Talk to {providerNames[provider]}</Text><Text style={s.muted}>Pair a runtime in Connections, complete the provider’s own sign-in, then send a message. Each provider keeps a separate conversation for this project.</Text>{!connection && <Pressable accessibilityRole="button" onPress={openConnections} style={s.button}><Text style={s.blue}>Set up connections</Text></Pressable>}</View>}
      {history.messages.map((m, i) => <View key={i} style={[s.message, m.role === 'user' && s.user]}><Text style={s.role}>{m.role === 'user' ? 'YOU' : providerNames[provider].toUpperCase()}</Text><Text selectable style={s.reply}>{m.text}</Text></View>)}
      {working && <ActivityIndicator color="#a7cff9" />}
    </ScrollView>
    {notice || history.problem ? <Text accessibilityRole="status" style={s.notice}>{notice || history.problem}</Text> : null}
    <View style={s.actions}>
      {history.pending ? <><Pressable accessibilityRole="button" disabled={working} onPress={() => watch(history.pending!)} style={s.button}><Text style={s.blue}>Check response</Text></Pressable><Pressable accessibilityRole="button" onPress={cancel} style={s.button}><Text style={s.text}>Stop request</Text></Pressable>{!working && <Pressable accessibilityRole="button" onPress={keepInterrupted} style={s.button}><Text style={s.text}>Keep as interrupted</Text></Pressable>}</> : <Pressable accessibilityRole="button" disabled={!ready || working} onPress={fresh} style={s.button}><Text style={s.text}>New conversation</Text></Pressable>}
    </View>
    <View style={s.composer}><TextInput accessibilityLabel="Chat message" multiline editable={ready && !history.pending && !history.problem} maxLength={10_000} value={draft} onChangeText={setDraft} placeholder={`Message ${providerNames[provider]}…`} placeholderTextColor="#9aabc2" style={s.input} /><Pressable accessibilityRole="button" disabled={!connection || !draft.trim() || !ready || working || !!history.pending || !!history.problem} onPress={send} style={[s.button, s.active, (!connection || !draft.trim() || working || !!history.pending || !!history.problem) && { opacity: 0.45 }]}><Text style={s.blue}>Send</Text></Pressable></View>
  </View>;
}

const s = StyleSheet.create({
  grow: { flex: 1, minHeight: 0 }, row: { flexDirection: 'row', gap: 6, flexWrap: 'wrap' }, header: { flexDirection: 'row', flexWrap: 'wrap', padding: 10, gap: 8, justifyContent: 'space-between', borderBottomWidth: 1, borderColor: '#2d3a4e' },
  context: { paddingHorizontal: 14, paddingVertical: 8, gap: 2 }, messages: { padding: 14, gap: 12 }, message: { backgroundColor: '#19212d', borderRadius: 8, padding: 13, gap: 7 }, user: { backgroundColor: '#20334b' },
  role: { color: '#a7cff9', fontSize: 10, fontWeight: '700' }, reply: { color: '#e5edf8', fontSize: 14, lineHeight: 22 }, text: { color: '#e5edf8', fontSize: 12 }, muted: { color: '#9aabc2', fontSize: 12, lineHeight: 19 }, blue: { color: '#a7cff9', fontSize: 12, fontWeight: '600' },
  button: { minHeight: 44, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 12, borderWidth: 1, borderColor: '#2d3a4e', borderRadius: 6 }, active: { backgroundColor: '#243d5c', borderColor: '#41658e' },
  heading: { color: '#e5edf8', fontSize: 18, fontWeight: '700' }, empty: { gap: 12, paddingVertical: 20 }, notice: { color: '#f0d39a', paddingHorizontal: 14, paddingVertical: 6, fontSize: 12, lineHeight: 19 },
  actions: { paddingHorizontal: 12, paddingVertical: 6, flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, composer: { flexDirection: 'row', gap: 8, padding: 12, borderTopWidth: 1, borderColor: '#2d3a4e', alignItems: 'flex-end' },
  input: { flex: 1, minHeight: 46, maxHeight: 150, color: '#e5edf8', backgroundColor: '#19212d', borderRadius: 6, padding: 12, fontSize: 14 },
});
