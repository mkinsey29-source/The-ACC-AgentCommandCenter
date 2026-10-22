import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { getRepository, GitHubAttachment, GitHubFile, githubError, listBranches, listFiles, listRepositories, readGitHubFile, Repository, repositoryName, resolveCommit } from './github';

export function GitHubPanel({ project, connected, attach, openConnections, onRepository }: { project: string; connected: boolean; attach: (file: GitHubAttachment) => void; openConnections: () => void; onRepository: (name: string) => void }) {
  const [name, setName] = useState(''), [repo, setRepo] = useState<Repository | null>(null);
  const [repos, setRepos] = useState<Repository[]>([]), [repoPage, setRepoPage] = useState(0), [moreRepos, setMoreRepos] = useState(false);
  const [branches, setBranches] = useState<string[]>([]), [branchPage, setBranchPage] = useState(0), [moreBranches, setMoreBranches] = useState(false);
  const [branch, setBranch] = useState(''), [commit, setCommit] = useState(''), [path, setPath] = useState('');
  const [files, setFiles] = useState<GitHubFile[]>([]), [preview, setPreview] = useState<GitHubAttachment | null>(null);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState(''), [ready, setReady] = useState(false);
  const current = useRef(true), working = useRef(false), queue = useRef(Promise.resolve());
  useEffect(() => {
    current.current = true;
    AsyncStorage.getItem(`acc.github.repository.v1:${project}`).then(raw => { if (current.current) { if (raw) setName(repositoryName(raw)); setReady(true); } }).catch(() => { if (current.current) { setReady(true); setNotice('Saved repository could not be opened. Enter it again.'); } });
    return () => { current.current = false; };
  }, [project]);
  async function run(fn: () => Promise<void>) {
    if (working.current || !ready) return;
    working.current = true; setBusy(true); setNotice('');
    try { await fn(); } catch (e) { if (current.current) setNotice(githubError(e)); }
    finally { working.current = false; if (current.current) setBusy(false); }
  }
  async function openRepo(value: string) {
    const r = await getRepository(value), sha = await resolveCommit(r.fullName, r.branch), items = await listFiles(r.fullName, sha);
    if (!current.current) return;
    setRepo(r); setName(r.fullName); setBranch(r.branch); setCommit(sha); setPath(''); setFiles(items); setPreview(null); setBranches([]); setBranchPage(0); setMoreBranches(false); onRepository(r.fullName);
    queue.current = queue.current.catch(() => {}).then(() => AsyncStorage.setItem(`acc.github.repository.v1:${project}`, r.fullName));
    await queue.current;
    if (current.current) setNotice('Repository read verified. Files are pinned to this commit until you refresh or switch branch.');
  }
  async function chooseBranch(value: string) {
    if (!repo) return;
    const sha = await resolveCommit(repo.fullName, value), items = await listFiles(repo.fullName, sha);
    if (!current.current) return;
    setBranch(value); setCommit(sha); setPath(''); setFiles(items); setPreview(null);
  }
  async function directory(value: string) {
    if (!repo) return;
    const items = await listFiles(repo.fullName, commit, value);
    if (current.current) { setPath(value); setFiles(items); setPreview(null); }
  }
  return <ScrollView style={s.grow} contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
    <View style={s.row}><Text style={s.heading}>GitHub</Text><Pressable accessibilityRole="button" onPress={openConnections} style={s.button}><Text style={s.blue}>{connected ? 'Manage connection' : 'Connect private repositories'}</Text></Pressable></View>
    <Text style={s.text}>Browse repository files, then attach one to your next ChatGPT message. ACC reads GitHub; it does not run code, push commits or merge pull requests.</Text>
    <View style={s.row}><TextInput accessibilityLabel="GitHub repository" value={name} editable={!busy && ready} autoCapitalize="none" autoCorrect={false} onChangeText={setName} maxLength={200} placeholder="owner/repository" placeholderTextColor="#9aabc2" style={s.input} /><Pressable accessibilityRole="button" disabled={busy || !ready || !name.trim()} onPress={() => run(() => openRepo(name))} style={s.button}><Text style={s.blue}>Open repository</Text></Pressable></View>
    {connected && <Pressable accessibilityRole="button" disabled={busy || (repoPage > 0 && !moreRepos)} onPress={() => run(async () => { const page = repoPage + 1, result = await listRepositories(page); if (current.current) { setRepos(old => [...new Map([...old, ...result.repositories].map(r => [r.fullName, r])).values()]); setRepoPage(page); setMoreRepos(result.more); } })} style={s.button}><Text style={s.blue}>{repoPage ? moreRepos ? 'More repositories' : 'Repository list loaded' : 'Choose from my repositories'}</Text></Pressable>}
    {repos.map(r => <Pressable accessibilityRole="button" key={r.fullName} disabled={busy} onPress={() => run(() => openRepo(r.fullName))} style={s.button}><Text style={s.text}>{r.fullName}{r.private ? ' · private' : ''}</Text></Pressable>)}
    {busy && <ActivityIndicator color="#a7cff9" />}{notice ? <Text role="status" accessibilityLiveRegion="polite" style={s.text}>{notice}</Text> : null}
    {repo && <>
      <Text selectable style={s.blue}>{repo.fullName} · {branch}</Text><Text selectable style={s.text}>Commit {commit}</Text>
      <View style={s.row}><Pressable accessibilityRole="button" disabled={busy || (branchPage > 0 && !moreBranches)} onPress={() => run(async () => { const page = branchPage + 1, result = await listBranches(repo.fullName, page); if (current.current) { setBranches(old => [...new Set([...old, ...result.branches])]); setBranchPage(page); setMoreBranches(result.more); } })} style={s.button}><Text style={s.blue}>{branchPage ? moreBranches ? 'More branches' : 'Branches loaded' : 'Choose branch'}</Text></Pressable><Pressable accessibilityRole="button" disabled={busy} onPress={() => run(() => chooseBranch(branch))} style={s.button}><Text style={s.blue}>Refresh revision</Text></Pressable></View>
      {branches.map(b => <Pressable key={b} accessibilityRole="radio" accessibilityState={{ checked: branch === b }} disabled={busy} onPress={() => run(() => chooseBranch(b))} style={s.button}><Text style={s.text}>{b === branch ? '● ' : ''}{b}</Text></Pressable>)}
      <View style={s.row}><Text style={s.text}>/{path}</Text>{path ? <Pressable accessibilityRole="button" disabled={busy} onPress={() => run(() => directory(path.split('/').slice(0,-1).join('/')))} style={s.button}><Text style={s.blue}>Parent folder</Text></Pressable> : null}</View>
      {files.map(f => <Pressable key={f.path} accessibilityRole="button" accessibilityLabel={`Open GitHub ${f.type}: ${f.path}`} disabled={busy} onPress={() => run(async () => { if (f.type === 'dir') return directory(f.path); const value = await readGitHubFile(repo.fullName, branch, commit, f.path); if (current.current) setPreview(value); })} style={s.button}><Text style={s.text}>{f.type === 'dir' ? 'Folder: ' : ''}{f.name}{f.type === 'file' ? ` · ${f.size} B` : ''}</Text></Pressable>)}
      {preview && <View style={s.preview}><Text style={s.heading}>{preview.path}</Text><Text selectable style={s.code}>{preview.text || '(Empty file)'}</Text><Text style={s.text}>Attach sends this file to your selected ChatGPT account when you press Send. Its contents are then saved in that conversation on this device.</Text><Pressable accessibilityRole="button" disabled={busy} onPress={() => attach(preview)} style={s.button}><Text style={s.blue}>Attach to next ChatGPT message</Text></Pressable></View>}
    </>}
  </ScrollView>;
}
const s = StyleSheet.create({ grow: { flex: 1 }, content: { padding: 14, gap: 10 }, heading: { color: '#e5edf8', fontSize: 16, fontWeight: '700' }, text: { color: '#9aabc2', fontSize: 12, lineHeight: 19 }, blue: { color: '#a7cff9', fontSize: 12, fontWeight: '600' }, code: { color: '#e5edf8', fontFamily: 'monospace', fontSize: 12, lineHeight: 18 }, row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'center' }, preview: { padding: 12, gap: 10, borderWidth: 1, borderColor: '#41658e', borderRadius: 6 }, input: { flex: 1, minWidth: 180, minHeight: 46, padding: 10, color: '#e5edf8', borderWidth: 1, borderColor: '#2d3a4e', borderRadius: 6 }, button: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 12, borderWidth: 1, borderColor: '#41658e', borderRadius: 6 } });
