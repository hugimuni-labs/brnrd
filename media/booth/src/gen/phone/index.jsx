import {ChatShot} from '../chat/Chat';
import script from '../chat/script.json';

// booth-gen's contract: phone[key] is a full-frame component, rendered by Trailer.jsx inside the slot's
// Sequence with {...props, sec, fps}. The shots and their ledger lines live in ../chat/script.json.
export const phone = Object.fromEntries(script.shots.map((s) => [s.key, (props) => <ChatShot shot={s.key} sec={props.sec} />]));
