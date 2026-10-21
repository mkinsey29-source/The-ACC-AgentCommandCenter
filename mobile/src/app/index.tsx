import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, AppState, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, useWindowDimensions, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { initialWorkspace, parseWorkspace, statuses, Task, vaultTemplates, Workspace } from '../model';
import { acknowledgeChatGPTPlan, chatGPTStatus, ChatGPTStatus, emptyChatGPT } from '../chatgpt';
import { Overlay } from '../Overlay';
import { Chat } from '../Chat';
import { Connections } from '../Connections';
import { Connection, loadConnection } from '../providers';
import { chooseFolder, openVault, readNote, saveNote, Vault } from '../vault';

const C = { bg: '#10151d', panel: '#19212d', line: '#2d3a4e', text: '#e5edf8', muted: '#9aabc2', blue: '#a7cff9', fill: '#243d5c', green: '#a8e0c0', amber: '#f0d39a' };
const key = 'acc.workspace.v1';
type Tab = 'Chat' | 'Agents' | 'Tasks' | 'Terminal' | 'Memory';
function Button({ title, onPress, active = false, disabled = false }: { title: string; onPress: () => void; active?: boolean; disabled?: boolean }) {
  return <Pressable accessibilityRole="button" accessibilityState={{ selected: active, disabled }} disabled={disabled} onPress={onPress} style={({ pressed }) => [s.button, active && s.buttonActive, (pressed || disabled) && { opacity: 0.55 }]}><Text style={[s.buttonText, active && { color: C.blue }]}>{title}</Text></Pressable>;
}
function Label({ children }: { children: React.ReactNode }) { return <Text style={s.label}>{children}</Text>; }

export default function WorkspaceScreen() {
  const { width } = useWindowDimensions();
  const wide = width >= 1000;
  const [workspace, setWorkspace] = useState<Workspace>(initialWorkspace);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [saveStatus, setSaveStatus] = useState('Opening local workspace…');
  const [project, setProject] = useState('acc');
  const [tab, setTab] = useState<Tab>('Chat');
  const [draft, setDraft] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [modal, setModal] = useState<'project' | 'providers' | null>(null);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [chatGPT, setChatGPT] = useState<ChatGPTStatus>(emptyChatGPT);
  async function refreshChatGPT() { setChatGPT(await chatGPTStatus()); }
  const [projectDraft, setProjectDraft] = useState('');
  const [vault, setVault] = useState<Vault | null>(null);
  const [note, setNote] = useState('consolidated_summary.md');
  const [noteText, setNoteText] = useState('');
  const [noteOriginal, setNoteOriginal] = useState('');
  const [noteReady, setNoteReady] = useState(false);
  const [noteBusy, setNoteBusy] = useState(false);
  const [noteError, setNoteError] = useState('');
  const [terminal, setTerminal] = useState(['ACC local console · build 5', 'Type help for workspace commands. Shell execution is not connected.']);
  const [command, setCommand] = useState('');
  const writeQueue = useRef(Promise.resolve());
  const noteQueue = useRef(0);

  useEffect(() => {
    let alive = true;
    AsyncStorage.getItem(key).then(raw => {
      if (!alive) return;
      const loaded = raw ? parseWorkspace(raw) : initialWorkspace;
      setWorkspace(loaded); setProject(loaded.projects[0].id); setSaveStatus('Saving on device…'); setReady(true);
    }).catch(() => { if (alive) setError('Saved workspace could not be opened. It has been preserved.'); });
    loadConnection().then(value => { if (alive) setConnection(value); }).catch(() => { if (alive) setError('Runtime pairing could not be opened. Reconnect through Connections.'); });
    openVault().then(v => { if (alive) setVault(v); }).catch(() => { if (alive) setNoteError('Cannot open the saved vault. Check the folder permission in Android settings.'); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    async function refresh() { try { const value = await chatGPTStatus(); if (alive) setChatGPT(value); } catch { if (alive) setError('ChatGPT account storage could not be opened. Saved data was preserved.'); } }
    void refresh();
    const subscription = AppState.addEventListener('change', state => { if (state === 'active') void refresh(); });
    const timer = modal === 'providers' || chatGPT.signingIn ? setInterval(() => void refresh(), 2000) : undefined;
    return () => { alive = false; subscription.remove(); if (timer) clearInterval(timer); };
  }, [modal, chatGPT.signingIn]);

  useEffect(() => {
    if (!ready) return;
    // Serialize writes so a slower old save cannot replace newer task state.
    const raw = JSON.stringify(workspace);
    writeQueue.current = writeQueue.current.catch(() => {}).then(() => AsyncStorage.setItem(key, raw)).then(() => {
      setSaveStatus('Saved on device'); setError('');
    }).catch(() => { setSaveStatus('Save failed'); setError('Changes are still in memory. Device storage could not be written.'); });
  }, [workspace, ready]);

  useEffect(() => {
    const sequence = ++noteQueue.current;
    if (!vault) return;
    readNote(vault, note).then(text => {
      if (sequence !== noteQueue.current) return;
      setNoteText(text); setNoteOriginal(text); setNoteReady(true); setNoteError('');
    }).catch(() => { if (sequence === noteQueue.current) setNoteError('Cannot read this note. The existing file has been preserved.'); });
  }, [vault, note]);

  const currentProject = workspace.projects.find(p => p.id === project)!;
  const tasks = workspace.tasks.filter(t => t.projectId === project);
  const selectedTask = tasks.find(t => t.id === selected);
  const done = tasks.filter(t => t.status === 'Done').length;
  const noteDirty = noteReady && noteText !== noteOriginal;
  function change(fn: (w: Workspace) => Workspace, event: string) {
    setSaveStatus('Saving on device…');
    setWorkspace(w => ({ ...fn(w), events: [event, ...w.events].slice(0, 60) }));
  }
  function addTask() {
    const title = draft.trim();
    if (!title || !ready) return;
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    change(w => ({ ...w, tasks: [...w.tasks, { id, projectId: project, title, status: 'Planned' }] }), `Task added: ${title}`);
    setDraft(''); setSelected(id); setTab('Tasks');
  }
  function advance(task: Task) {
    const status = statuses[(statuses.indexOf(task.status) + 1) % statuses.length];
    change(w => ({ ...w, tasks: w.tasks.map(t => t.id === task.id ? { ...t, status } : t) }), `${task.title} → ${status} (manual)`);
  }
  async function save() {
    if (!vault || !noteReady || noteBusy) return;
    setNoteBusy(true);
    const snapshot = noteText;
    try { await saveNote(vault, note, snapshot, noteOriginal); setNoteOriginal(snapshot); setNoteError(''); }
    catch (e) { setNoteError(e instanceof Error ? e.message : 'Save failed. Your draft is still here.'); }
    finally { setNoteBusy(false); }
  }
  async function folder() {
    if (!vault || noteBusy || noteDirty) return;
    setNoteBusy(true);
    try { const chosen = await chooseFolder(vault); if (chosen) { setNoteReady(false); setVault(chosen); } }
    catch (e) { setNoteError(e instanceof Error ? e.message : 'Folder selection failed. The current vault is unchanged.'); }
    finally { setNoteBusy(false); }
  }
  function runLocalCommand() {
    const value = command.trim();
    if (!value) return;
    const output = value === 'help' ? 'help · status · tasks · clear\nThese commands inspect the local workspace; they do not start a shell.'
      : value === 'status' ? `${currentProject.name}: ${tasks.length} tasks, ${done} done. Providers: ${connection ? 'runtime paired; check Connections for sign-in' : 'runtime not paired'}.`
      : value === 'tasks' ? tasks.map(t => `[${t.status}] ${t.title}`).join('\n') || 'No tasks.'
      : 'Unknown local command. Type help. A terminal runtime is not connected yet.';
    setTerminal(t => value === 'clear' ? [] : [...t, `acc > ${value}`, output].slice(-80)); setCommand('');
  }

  const memory = <View style={s.grow}>
    <View style={s.sectionHeader}><View style={s.grow}><Text style={s.heading}>Sentry</Text><Text style={s.muted}>{vault?.label || 'Opening local files…'}</Text></View><Button title="Choose folder" onPress={folder} disabled={!vault || noteBusy || noteDirty || Platform.OS !== 'android'} /></View>
    <ScrollView horizontal style={s.noteTabs} contentContainerStyle={{ gap: 4 }}>
      {Object.keys(vaultTemplates).map(name => <Button key={name} title={name} active={note === name} disabled={noteBusy || noteDirty || name === note} onPress={() => { setNoteReady(false); setNote(name); }} />)}
    </ScrollView>
    {noteError ? <Text accessibilityRole="alert" style={s.warning}>{noteError}</Text> : null}
    <TextInput accessibilityLabel="Note editor" editable={noteReady && !noteBusy} multiline textAlignVertical="top" value={noteText} onChangeText={setNoteText} style={s.editor} />
    <View style={s.sectionHeader}><Text style={s.muted}>{noteDirty ? 'Unsaved draft · save before switching notes' : 'Markdown · local storage'}</Text><Button title={noteBusy ? 'Saving…' : 'Save note'} active onPress={save} disabled={!noteReady || noteBusy || !noteDirty} /></View>
  </View>;

  return <SafeAreaView style={s.safe}>
    <KeyboardAvoidingView style={s.grow} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={s.grow} accessibilityElementsHidden={modal !== null || chatGPT.welcomePending} importantForAccessibility={modal !== null || chatGPT.welcomePending ? 'no-hide-descendants' : 'auto'}>
      <View style={s.header}><View style={s.brandMark}><Text style={s.brandIcon}>A</Text></View><View style={s.grow}><Text style={s.brand}>ACC <Text style={s.brandSub}>/ COMMAND CENTER</Text></Text><Text style={s.muted}>{currentProject?.name || 'Workspace'} · Android build 5</Text></View><Button title="Connections" onPress={() => setModal('providers')} /></View>
      <View style={s.projectRow}><ScrollView horizontal contentContainerStyle={{ gap: 6, alignItems: 'center' }}>{workspace.projects.map(p => <Button key={p.id} title={p.name} active={p.id === project} onPress={() => { setProject(p.id); setSelected(null); }} />)}<Button title="+ Project" disabled={!ready} onPress={() => setModal('project')} /></ScrollView></View>
      {error ? <Text accessibilityRole="alert" style={s.warning}>{error}</Text> : null}
      {!ready ? <ActivityIndicator color={C.blue} style={{ margin: 10 }} /> : null}
      <View style={s.body}>
        {wide && tab !== 'Terminal' && <View style={s.leftRail}><Label>WORKSPACE</Label><Text style={s.heading}>{currentProject?.name}</Text><Text style={s.muted}>Local project · chat and planning</Text><View style={s.divider} /><Label>NEEDS ATTENTION</Label><Pressable accessibilityRole="button" onPress={() => setModal('providers')} style={s.attention}><Text style={s.amber}>{connection || chatGPT.accounts.some(a => a.signedIn) ? 'Open provider connections' : 'Connect your first provider'}</Text><Text style={s.muted}>ChatGPT account · optional runtimes</Text></Pressable><View style={s.divider} /><Label>PROGRESS</Label><Text style={s.bigNumber}>{done}<Text style={s.smallNumber}> / {tasks.length}</Text></Text><Text style={s.muted}>Tasks marked done</Text><View style={s.progress}><View style={[s.progressFill, { width: `${tasks.length ? done / tasks.length * 100 : 0}%` }]} /></View><View style={s.divider} /><Label>LOCAL MEMORY</Label><Text style={s.muted}>Readable Markdown. Your decisions, lessons and handoffs stay on this device.</Text><Button title="Open Sentry" onPress={() => setTab('Memory')} /></View>}
        <View style={s.center}>
          <View style={s.tabs}>{(['Chat', 'Agents', 'Tasks', 'Terminal', 'Memory'] as Tab[]).map(name => <Pressable key={name} accessibilityRole="tab" accessibilityState={{ selected: tab === name }} onPress={() => setTab(name)} style={[s.tab, tab === name && s.tabActive]}><Text style={[s.tabText, tab === name && { color: C.blue }]}>{name.toUpperCase()}</Text></Pressable>)}</View>
          {tab === 'Chat' ? <Chat account={chatGPT.accounts.find(a => a.id === chatGPT.active)} connection={connection} project={project} openConnections={() => setModal('providers')} /> : tab === 'Memory' ? memory : tab === 'Tasks' ? <>
            <View style={s.sectionHeader}><View><Text style={s.heading}>Task board</Text><Text style={s.muted}>{tasks.length} tasks · {done} done · saved locally</Text></View><Text style={s.badge}>MANUAL</Text></View>
            <ScrollView style={s.grow} contentContainerStyle={{ padding: 14, gap: 10 }}>
              {tasks.map((task, i) => <Pressable accessibilityRole="button" accessibilityLabel={`Select task: ${task.title}`} key={task.id} onPress={() => setSelected(task.id)} style={[s.task, selected === task.id && s.taskSelected]}><View style={[s.row, { gap: 12 }]}><Text style={s.taskNumber}>{String(i + 1).padStart(2, '0')}</Text><View style={s.grow}><Text style={s.taskTitle}>{task.title}</Text><Text style={s.muted}>{currentProject.name} · Unassigned</Text></View></View><View style={[s.row, { justifyContent: 'space-between', marginTop: 10 }]}><Text style={[s.status, { color: task.status === 'Done' ? C.green : task.status === 'Review' ? C.amber : C.blue }]}>{task.status}</Text><Button title="Change status" disabled={!ready} onPress={() => advance(task)} /></View></Pressable>)}
              {!tasks.length && <View style={s.empty}><Text style={s.heading}>A fresh project</Text><Text style={s.muted}>Add your first task below.</Text></View>}
            </ScrollView>
          </> : tab === 'Agents' ? <ScrollView contentContainerStyle={{ padding: 18, gap: 14 }}><Text style={s.heading}>Your agent team</Text><Text style={s.muted}>Chat connections are available now. Task execution and agent assignments are the next slice. Roles can share a provider.</Text>{[{ name: 'Coordinator', initials: 'CO', detail: 'Break down work and keep the project moving.' }, { name: 'Builder', initials: 'BU', detail: 'Implement tasks in a dedicated workspace.' }, { name: 'Reviewer', initials: 'RE', detail: 'Check the changes and evidence in a fresh session.' }].map(agent => <View key={agent.name} style={s.agent}><View style={s.avatar}><Text style={s.avatarText}>{agent.initials}</Text></View><View style={s.grow}><Text style={s.taskTitle}>{agent.name}</Text><Text style={s.muted}>{agent.detail}</Text><Text style={s.status}>Not connected</Text></View></View>)}<Button title="Provider connections" active onPress={() => setModal('providers')} /></ScrollView> : <View style={s.grow}><View style={s.sectionHeader}><Text style={s.heading}>Local console</Text><Text style={s.badge}>NO SHELL ATTACHED</Text></View><ScrollView style={s.console}>{terminal.map((line, i) => <Text selectable key={i} style={s.consoleText}>{line}</Text>)}</ScrollView><View style={s.composer}><Text style={s.consoleText}>acc &gt;</Text><TextInput accessibilityLabel="Local console command" autoCapitalize="none" autoCorrect={false} value={command} onChangeText={setCommand} onSubmitEditing={runLocalCommand} style={s.input} placeholder="help" placeholderTextColor={C.muted} /><Button title="Run" onPress={runLocalCommand} /></View></View>}
        </View>
        {wide && tab === 'Tasks' && <View style={s.rightRail}><Label>TASK DETAILS</Label><Text style={s.heading}>{selectedTask?.title || 'Select a task'}</Text><Text style={s.muted}>{selectedTask ? `${selectedTask.status} · manual status` : 'Inspect a task from the board.'}</Text><View style={s.divider} /><Label>EXECUTION</Label><Text style={s.muted}>Tasks are manual planning items. Use Chat for provider conversations; automated task execution is not connected yet.</Text><View style={s.divider} /><Label>PLAY-BY-PLAY</Label>{workspace.events.slice(0, 5).map((event, i) => <Text key={i} style={s.event}>{event}</Text>)}</View>}
      </View>
      {tab === 'Tasks' && <View style={s.bottomRail}><View style={[s.row, { gap: 8 }]}><View style={s.dot} /><Text style={s.label}>ADD A TASK</Text><Text style={[s.muted, s.grow]} numberOfLines={1}>{saveStatus}</Text></View><View style={s.composer}><TextInput accessibilityLabel="New task" editable={ready} value={draft} maxLength={500} onChangeText={setDraft} onSubmitEditing={addTask} style={s.input} placeholder="Enter a new task…" placeholderTextColor={C.muted} /><Button title="Add task" active disabled={!draft.trim() || !ready} onPress={addTask} /></View>{!wide && <Text numberOfLines={1} style={s.muted}>{workspace.events[0]}</Text>}</View>}
      </View>
      <Overlay visible={modal !== null || chatGPT.welcomePending} onRequestClose={() => { if (chatGPT.welcomePending) void acknowledgeChatGPTPlan().then(setChatGPT).catch(() => setError('Could not save the plan notice.')); else setModal(null); }}><View style={s.modalBackdrop}><View style={s.modal}>{chatGPT.welcomePending ? <><Text style={s.heading}>You’re using your ChatGPT plan</Text><Text style={s.muted}>Eligible AI requests in ACC use your ChatGPT plan or credits allowance. Manage ACC’s access and usage limits in ChatGPT settings.</Text><Button title="Got it" active onPress={() => acknowledgeChatGPTPlan().then(setChatGPT).catch(() => setError('Could not save the plan notice.'))} /></> : <><View style={s.sectionHeader}><Text style={s.heading}>{modal === 'project' ? 'New project' : 'Provider connections'}</Text><Button title="Close" onPress={() => setModal(null)} /></View>{modal === 'project' ? <><TextInput accessibilityLabel="Project name" autoFocus value={projectDraft} maxLength={80} onChangeText={setProjectDraft} placeholder="Project name" placeholderTextColor={C.muted} style={s.projectInput} /><Button title="Create project" active disabled={!projectDraft.trim()} onPress={() => { const name = projectDraft.trim(); const id = Date.now().toString(36); change(w => ({ ...w, projects: [...w.projects, { id, name }] }), `Project created: ${name}`); setProject(id); setProjectDraft(''); setModal(null); }} /></> : modal === 'providers' ? <Connections connection={connection} onChange={setConnection} chatGPT={chatGPT} refreshChatGPT={refreshChatGPT} /> : null}</>}</View></View></Overlay>
    </KeyboardAvoidingView>
  </SafeAreaView>;
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.bg }, grow: { flex: 1, minHeight: 0 }, row: { flexDirection: 'row', alignItems: 'center' },
  header: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingVertical: 12, borderBottomWidth: 1, borderColor: C.line },
  brandMark: { width: 38, height: 38, backgroundColor: C.fill, borderRadius: 8, alignItems: 'center', justifyContent: 'center' }, brandIcon: { color: C.blue, fontSize: 25, fontWeight: '800' }, brand: { color: C.text, fontWeight: '800', fontSize: 20, letterSpacing: 1 }, brandSub: { color: C.muted, fontSize: 10, fontWeight: '500', letterSpacing: 1 },
  projectRow: { paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: 1, borderColor: C.line }, body: { flex: 1, flexDirection: 'row', minHeight: 0 },
  leftRail: { width: 210, padding: 16, gap: 8, borderRightWidth: 1, borderColor: C.line }, rightRail: { width: 240, padding: 16, gap: 9, borderLeftWidth: 1, borderColor: C.line }, center: { flex: 1, minWidth: 0, minHeight: 0 },
  tabs: { flexDirection: 'row', borderBottomWidth: 1, borderColor: C.line, backgroundColor: '#131b26' }, tab: { flex: 1, minHeight: 48, alignItems: 'center', justifyContent: 'center', borderBottomWidth: 2, borderColor: 'transparent' }, tabActive: { borderColor: C.blue, backgroundColor: '#1b2b40' }, tabText: { color: C.muted, fontSize: 11, fontWeight: '700', letterSpacing: 0.7 },
  label: { color: C.muted, fontSize: 10, fontWeight: '700', letterSpacing: 1.4 }, heading: { color: C.text, fontSize: 18, fontWeight: '700' }, muted: { color: C.muted, fontSize: 12, lineHeight: 19 }, amber: { color: C.amber, fontSize: 13, fontWeight: '600' },
  button: { paddingHorizontal: 12, minHeight: 44, justifyContent: 'center', alignItems: 'center', borderRadius: 6, borderWidth: 1, borderColor: C.line }, buttonActive: { backgroundColor: C.fill, borderColor: '#41658e' }, buttonText: { color: C.text, fontSize: 12, fontWeight: '600' },
  sectionHeader: { paddingHorizontal: 16, paddingVertical: 12, flexDirection: 'row', gap: 12, alignItems: 'center', justifyContent: 'space-between' }, badge: { color: C.muted, fontSize: 9, letterSpacing: 1, backgroundColor: C.panel, padding: 7, borderRadius: 4 },
  task: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 8, padding: 14 }, taskSelected: { borderColor: '#6f9ed0', backgroundColor: '#1c2a3b' }, taskNumber: { color: '#6b829e', fontSize: 15, fontWeight: '600' }, taskTitle: { color: C.text, fontSize: 15, fontWeight: '600', lineHeight: 22 }, status: { fontSize: 11, color: C.blue, marginTop: 4 },
  divider: { height: 1, backgroundColor: C.line, marginVertical: 14 }, attention: { borderLeftWidth: 2, borderColor: C.amber, paddingLeft: 10, gap: 5, paddingVertical: 6 }, bigNumber: { color: C.text, fontSize: 38, fontWeight: '600' }, smallNumber: { fontSize: 18, color: C.muted }, progress: { height: 4, backgroundColor: C.line, borderRadius: 3, marginTop: 8 }, progressFill: { height: 4, backgroundColor: C.blue }, event: { color: C.muted, lineHeight: 19, fontSize: 12, paddingVertical: 9, borderBottomWidth: 1, borderColor: C.line },
  bottomRail: { padding: 12, gap: 7, borderTopWidth: 1, borderColor: C.line, backgroundColor: '#131b26' }, dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: C.green }, composer: { flexDirection: 'row', gap: 10, alignItems: 'center' }, input: { flex: 1, minWidth: 0, minHeight: 46, padding: 10, color: C.text, fontSize: 14, backgroundColor: C.bg, borderRadius: 6, borderWidth: 1, borderColor: C.line },
  agent: { backgroundColor: C.panel, padding: 16, borderRadius: 8, flexDirection: 'row', gap: 14, alignItems: 'center', borderWidth: 1, borderColor: C.line }, avatar: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center', backgroundColor: C.fill, borderRadius: 10 }, avatarText: { color: C.blue, fontSize: 12, fontWeight: '700' },
  editor: { flex: 1, padding: 16, margin: 12, marginBottom: 0, borderWidth: 1, borderColor: C.line, borderRadius: 6, backgroundColor: '#0c121b', color: C.text, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 13, lineHeight: 21 }, noteTabs: { flexGrow: 0, minHeight: 46, maxHeight: 50, paddingHorizontal: 12 },
  console: { flex: 1, padding: 16, backgroundColor: '#0c121b' }, consoleText: { color: C.green, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 12, lineHeight: 21 }, empty: { padding: 20, gap: 8 },
  warning: { color: C.amber, fontSize: 12, lineHeight: 18, padding: 12, backgroundColor: '#2c281b' }, modalBackdrop: { flex: 1, backgroundColor: '#000000b8', justifyContent: 'center', alignItems: 'center', padding: 20 }, modal: { width: '100%', maxWidth: 620, maxHeight: '92%', backgroundColor: C.panel, padding: 18, gap: 16, borderRadius: 12, borderWidth: 1, borderColor: C.line }, projectInput: { color: C.text, borderWidth: 1, borderColor: C.line, padding: 12, borderRadius: 6, minHeight: 48 }, provider: { borderWidth: 1, borderColor: C.line, borderRadius: 6, padding: 14, gap: 6 },
});
