import { requireOptionalNativeModule } from 'expo';
import { Linking, Platform } from 'react-native';

export type ChatGPTAccount = { id: string; label: string; signedIn: boolean };
export type ChatGPTStatus = { accounts: ChatGPTAccount[]; active: string; welcomePending: boolean; signingIn: boolean; message: string };
export type ChatGPTModel = { id: string; name: string };
type NativeChatGPT = {
  status(): Promise<string>;
  acknowledgePlanUse(): Promise<string>;
  beginSignIn(id: string | null): Promise<string>;
  cancelSignIn(): Promise<void>;
  selectAccount(id: string): Promise<string>;
  signOut(id: string): Promise<string>;
  models(): Promise<string>;
  chat(id: string, accountId: string, model: string, messages: string): Promise<string>;
  cancelChat(id: string): Promise<void>;
};
const native = Platform.OS === 'android' ? requireOptionalNativeModule<NativeChatGPT>('AccChatGPT') : null;
export const supportsChatGPT = !!native;
export const emptyChatGPT: ChatGPTStatus = { accounts: [], active: '', welcomePending: false, signingIn: false, message: '' };
function module(): NativeChatGPT {
  if (!native) throw new Error('Direct ChatGPT sign-in needs the Android APK with direct sign-in. It is not available in the browser preview or Expo Go.');
  return native;
}
export async function chatGPTStatus(): Promise<ChatGPTStatus> {
  return native ? JSON.parse(await native.status()) : emptyChatGPT;
}
export async function signInChatGPT(accountId: string | null = null) {
  const n = module(); const { url } = JSON.parse(await n.beginSignIn(accountId));
  // Always OpenAI in the system browser; never embed credentials in a WebView.
  if (typeof url !== 'string' || !url.startsWith('https://auth.openai.com/api/accounts/authorize?')) { await n.cancelSignIn(); throw new Error('Could not open the official sign-in page.'); }
  try { await Linking.openURL(url); } catch { await n.cancelSignIn(); throw new Error('Could not open your browser. Choose a default browser in Android settings and try again.'); }
}
export const cancelChatGPTSignIn = () => module().cancelSignIn();
export const selectChatGPTAccount = async (id: string): Promise<ChatGPTStatus> => JSON.parse(await module().selectAccount(id));
export const signOutChatGPT = (id: string) => module().signOut(id);
export async function chatGPTModels(): Promise<{ accountId: string; models: ChatGPTModel[] }> { return JSON.parse(await module().models()); }
export const sendChatGPT = (id: string, accountId: string, model: string, messages: { role: string; text: string }[]) => module().chat(id, accountId, model, JSON.stringify(messages));
export const cancelChatGPT = (id: string) => module().cancelChat(id);
// Native errors are fixed messages and never include remote response bodies or credentials.
export const chatGPTError = (error: unknown) => error instanceof Error ? error.message : 'ChatGPT could not complete this action. Try again deliberately.';

export const acknowledgeChatGPTPlan = async (): Promise<ChatGPTStatus> => JSON.parse(await module().acknowledgePlanUse());
