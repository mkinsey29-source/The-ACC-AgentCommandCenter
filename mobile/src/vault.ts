import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';
import * as FS from 'expo-file-system/legacy';
import { vaultTemplates } from './model';

export type Vault = { label: string; files: Record<string, string> };
const vaultKey = 'acc.vault.location.v1';
const webKey = 'acc.vault.web.v1';

// A fixed filename allowlist prevents note names becoming filesystem paths.
export async function readNote(vault: Vault, name: string): Promise<string> {
  if (!(name in vaultTemplates)) throw new Error('Unsupported note.');
  if (Platform.OS === 'web') {
    const notes = JSON.parse(await AsyncStorage.getItem(webKey) || '{}');
    return notes[name] ?? vaultTemplates[name];
  }
  return FS.readAsStringAsync(vault.files[name]);
}

export async function saveNote(vault: Vault, name: string, text: string, expected: string) {
  if (await readNote(vault, name) !== expected) {
    throw new Error('This note changed outside the editor. Reopen it before saving. Your draft is still here.');
  }
  if (Platform.OS === 'web') {
    const notes = JSON.parse(await AsyncStorage.getItem(webKey) || '{}');
    notes[name] = text;
    await AsyncStorage.setItem(webKey, JSON.stringify(notes));
  } else {
    // Preserve a readable local recovery copy before touching the selected file.
    // SAF providers do not promise atomic replacement; this is a backup, not a lock.
    if (!FS.documentDirectory) throw new Error('Backup storage is unavailable. The note was not changed.');
    const backups = FS.documentDirectory + 'Sentry-backups/';
    await FS.makeDirectoryAsync(backups, { intermediates: true });
    await FS.writeAsStringAsync(backups + Date.now() + '-' + name, expected);
    await FS.writeAsStringAsync(vault.files[name], text);
    if (await readNote(vault, name) !== text) throw new Error('The saved note could not be verified.');
  }
}

export async function openVault(): Promise<Vault> {
  const saved = await AsyncStorage.getItem(vaultKey);
  if (saved) {
    const vault = JSON.parse(saved) as Vault;
    // Recheck access after process death or a revoked folder grant.
    for (const name of Object.keys(vaultTemplates)) await readNote(vault, name);
    return vault;
  }
  const files: Record<string, string> = {};
  if (Platform.OS !== 'web') {
    if (!FS.documentDirectory) throw new Error('Device storage is unavailable.');
    const root = FS.documentDirectory + 'Sentry/';
    await FS.makeDirectoryAsync(root, { intermediates: true });
    for (const [name, content] of Object.entries(vaultTemplates)) {
      files[name] = root + name;
      if (!(await FS.getInfoAsync(files[name])).exists) await FS.writeAsStringAsync(files[name], content);
    }
  }
  const vault = { label: Platform.OS === 'web' ? 'Browser preview storage' : 'On this device · app folder', files };
  await AsyncStorage.setItem(vaultKey, JSON.stringify(vault));
  return vault;
}

export async function chooseFolder(current: Vault): Promise<Vault | null> {
  if (Platform.OS !== 'android') throw new Error('Choose folder is available in the Android APK.');
  const permission = await FS.StorageAccessFramework.requestDirectoryPermissionsAsync();
  if (!permission.granted) return null;
  // Always create a new child folder; never overwrite an existing user's vault.
  const folderName = 'Sentry-' + Date.now();
  const root = await FS.StorageAccessFramework.makeDirectoryAsync(permission.directoryUri, folderName);
  const files: Record<string, string> = {};
  for (const name of Object.keys(vaultTemplates)) {
    const text = await readNote(current, name);
    files[name] = await FS.StorageAccessFramework.createFileAsync(root, name, 'text/markdown');
    await FS.writeAsStringAsync(files[name], text);
    if (await FS.readAsStringAsync(files[name]) !== text) throw new Error('Folder copy could not be verified.');
  }
  const vault = { label: folderName, files };
  await AsyncStorage.setItem(vaultKey, JSON.stringify(vault));
  return vault;
}
