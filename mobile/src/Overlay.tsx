import { useEffect } from 'react';
import { BackHandler, StyleSheet, View } from 'react-native';

/** Keep workspace panels in the existing Android window, including hardware-back dismissal. */
export function Overlay({ visible, onRequestClose, children }: { visible: boolean; onRequestClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    if (!visible) return;
    const subscription = BackHandler.addEventListener('hardwareBackPress', () => { onRequestClose(); return true; });
    return () => subscription.remove();
  }, [visible, onRequestClose]);
  if (!visible) return null;
  return <View testID="acc-overlay" role="dialog" accessibilityViewIsModal style={s.overlay}>{children}</View>;
}
const s = StyleSheet.create({ overlay: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, zIndex: 100, elevation: 32 } });
