from pathlib import Path
import re

src = Path('data/automated_runs/remake_1793345519_99f0b2/kich_ban_EN_remake_1793345519_99f0b2.txt')
out = Path('data/automated_runs/remake_1793345519_99f0b2/kich_ban_EN_rewritten_visual_prompts.md')
text = src.read_text(encoding='utf-8')
style = ('Simple flat 2D cartoon explainer, crisp smooth bold black outlines, clean vector edges, '
         'flat colours with exactly one flat grey side shadow, muted-but-clear dusty blue, muted navy, '
         'off-white and warm earth palette, one muted-red accent, 16:9. ')
negative = (' No photorealism, no 3D render, no gradients, no crayon texture, no paper grain, no sketchy lines, '
            'no blurry image, no extra limbs, no realistic fingers, no detailed clothing folds, no cluttered background, '
            'no tiny unreadable text, no garbled text, no crowd with peach skin, no inconsistent hairstyle, no changing outfit.')

def prompt(n, i):
    s = n.lower()
    # Locked character sheet: use this exact identity whenever Wang appears.
    wang = 'Wang Chuan-fu, peach skin, oversized round head, short neat black hair, tiny dot eyes, small oval body, dark plain outfit, thin stick limbs and blob hands'
    wang_child = 'young Wang, peach skin, oversized round head, short neat black hair, small plain school outfit, tiny dot eyes, oval body, stick limbs and blob hands'
    crowd = 'secondary figures have plain white faces, one flat grey side shadow, simple flat hair and repeated oval bodies'
    special = {
        12: ("quote or dialogue shot", "Wang's older brother, peach-faced with short neat black hair and a simple dark outfit, reassuring young Wang", 'places one blob hand on young Wang’s shoulder', 'warm, protective and reassuring', 'Foreground: a cropped worried family member frames the left edge; midground: the brother and young Wang beside an open schoolbook; background: a small modest home with a repeated shelf pattern.', 'The viewer reads the white thought bubble first, then the brother’s protective gesture.', 'Large handwritten quote in a white cloud bubble: “Who will care for us?” followed by a second smaller bubble: “I will.”'),
        27: ('diagram / infographic shot', 'three oversized battery price tags held above a locked global supply chain', 'the price tags rise year after year while a thick black gate stays closed', 'oppressive and unchallenged', 'Foreground: a cropped locked gate; midground: the three price tags; background: repeated battery crates connected across a muted world map.', 'The viewer sees the rising red price line first, then the gate and controlled supply route.', 'Large labels only: “PRICE UP” and “NO CHALLENGE”.'),
        38: ('scene shot / wide storytelling shot', 'a clean sponsor card for ChatLLM by Abacus AI with one large laptop screen', 'the laptop screen flips toward the viewer in a controlled fourth-wall reveal', 'playful, self-aware and interruptive', 'Foreground: cropped edge of a video timeline; midground: the laptop and sponsor card; background: a simple muted desk with one microphone.', 'The sponsor card is seen first, then the laptop interface.', 'Large readable text: “SPONSORED BY CHATLLM” and “ABACUS AI”.'),
        39: ('scene shot / wide storytelling shot', 'an exhausted user with white-faced simplified features and a nearly empty wallet', 'the user reaches toward a row of oversized AI subscription tabs', 'overwhelmed and financially drained', 'Foreground: a cropped empty wallet; midground: the user and laptop; background: repeated browser tabs each marked with a large dollar symbol.', 'The empty wallet is seen first, then the multiplying tabs.', 'Large label: “$20 EACH”.'),
        40: ('diagram / infographic shot', 'one large affordable ChatLLM bundle box beside several separate expensive plan boxes', 'the separate boxes compress into one bundle marked with a ten-dollar price', 'relieved and economical', 'Foreground: cropped expensive receipt; midground: the single bundle box; background: muted plan boxes receding in a row.', 'The large “$10” is seen first, then the bundle replacing the separate plans.', 'Large readable text: “$10 BUNDLE”.'),
        41: ('scene shot / wide storytelling shot', 'one clean dashboard containing GPT, Claude and Gemini panels', 'three panels slide into one unified dashboard without changing the camera', 'clear and convenient', 'Foreground: cropped laptop bezel; midground: the unified dashboard; background: a simple desk and repeated muted tabs fading away.', 'The unified dashboard is seen first, then the vanished tab clutter.', 'Large panel labels: “GPT”, “CLAUDE”, “GEMINI”.'),
        42: ('scene shot / wide storytelling shot', 'a new model cube entering an open slot in the ChatLLM dashboard', 'the cube glides once into the slot and stops', 'smooth and effortless', 'Foreground: cropped server slot edge; midground: the labelled new-model cube; background: repeated quiet dashboard modules.', 'The new model cube is seen first, then the open slot.', 'Large label: “NEW MODEL”.'),
        43: ('diagram / infographic shot', 'a smart router node connected to three differently labelled AI model paths', 'one red request dot travels along the best path and stops at the selected model', 'intelligent and decisive', 'Foreground: cropped request card; midground: router node and three paths; background: simple muted server panels.', 'The router is seen first, then the selected path.', 'Large labels: “REQUEST”, “BEST MODEL”.'),
        44: ('scene shot / wide storytelling shot', 'one user dashboard producing a document, presentation, image and video panel', 'four outputs appear from one clearly visible click point', 'productive and capable', 'Foreground: cropped mouse and hand blob; midground: the dashboard; background: repeated muted project cards.', 'The single click point is seen first, then the four outputs.', 'Short labels: “DOCS”, “SLIDES”, “IMAGE”, “VIDEO”.'),
        45: ('scene shot / wide storytelling shot', 'a rough idea card becoming a complete website with a payment panel', 'one block locks into the website layout and the payment panel lights up', 'surprised and empowering', 'Foreground: cropped rough sketch; midground: the finished website; background: simple repeated code blocks.', 'The unfinished sketch is seen first, then the completed site.', 'Large label: “BUILD WEBSITE”.'),
        46: ('scene shot / wide storytelling shot', 'a simple personal assistant character beside a day-to-night clock and task list', 'one task receives a check mark while the clock remains running', 'reliable and tireless', 'Foreground: cropped task list; midground: assistant and clock; background: repeated day/night window shapes.', 'The assistant is seen first, then the continuous clock.', 'Large label: “DAY AND NIGHT”.'),
        47: ('scene shot / wide storytelling shot', 'a large downward arrow pointing to a clear link button below the sponsor card', 'the arrow descends once and settles over the button', 'direct and inviting', 'Foreground: cropped lower-screen edge; midground: wallet and link button; background: muted dashboard elements.', 'The downward arrow is seen first, then the link button.', 'Large label: “BUILD SMARTER”.'),
        73: ('quote or dialogue shot', 'Lu Shang-yang, white-faced secondary figure, confronting Wang across a boardroom table', 'Lu raises one document toward Wang in a single challenging gesture', 'skeptical, alarmed and confrontational', 'Foreground: a cropped white-faced manager frames the right edge; midground: Lu and Wang; background: repeated boardroom chairs and the acquisition document.', 'The white thought bubble is seen first, then Lu’s challenge and Wang’s still posture.', 'Large handwritten quote in a white cloud bubble: “You built a battery empire from nothing. Do you really want to tear it all down now?”'),
        85: ('scene shot / wide storytelling shot', f'{wang} returning alone to his private laboratory', 'quietly opens the laboratory door and turns toward a single workbench', 'silent, disciplined and intensely focused', 'Foreground: cropped newspaper headlines and arguing silhouettes; midground: Wang at the laboratory door; background: repeated shelves, battery samples and one desk lamp.', 'The silent figure is seen first, then the laboratory workbench; no speech bubble.', 'No on-screen text.'),
    }
    if i in special:
        return (f'{style} Shot type: {special[i][0]}. Main subject: {special[i][1]}. Action: {special[i][2]}. Emotion: {special[i][3]}. '
                f'{special[i][4]} {special[i][5]} On-screen text: {special[i][6]} '
                'Video motion: first 30% establishes the frame, middle 40% contains only the stated slow motion, final 30% holds a stable readable frame; static camera. '
                f'{negative}')
    subject = 'the story subject'
    action = 'holds the central object in a clear readable pose'
    emotion = 'focused and determined'
    setting = 'Foreground: a large cropped object frames the edge; midground: the focal subject; background: a simple repeated row of relevant props creates depth.'
    hook = 'The first read is the single focal object, followed by the subject and supporting context.'
    shot = 'scene shot / wide storytelling shot'
    accent = 'the muted-red accent marks the key risk or result'
    # Convert the narration into a concrete, shot-specific visual rather than a generic placeholder.
    if 'wang chuan-fu' in s or 'wang' in s:
        subject = wang_child if any(x in s for x in ['boy', 'child', 'young wang', 'orphan']) else wang
    elif 'tesla' in s:
        subject = 'a large simplified Tesla electric car silhouette facing the narrated market comparison'
    elif 'byd' in s or 'b-y-d' in s:
        subject = 'a large BYD battery, car, factory, or market marker that directly represents the narrated milestone'
    elif 'battery' in s or 'cell' in s or 'blade' in s:
        subject = 'an oversized battery cell or Blade battery pack with a clear silhouette and only three internal details'
    elif 'factory' in s or 'production' in s or 'workers' in s:
        subject = 'a compact factory production line with one clearly highlighted station and repeated worker figures'
    elif 'car' in s or 'vehicle' in s or 'automotive' in s:
        subject = 'one oversized compact electric car with a clear battery or production cue'
    elif 'market' in s or 'price' in s or 'cost' in s or 'profit' in s:
        subject = 'one oversized comparison chart with two clearly separated market or cost positions'
    elif 'map' in s or 'china' in s or 'japan' in s or 'hungary' in s or 'brazil' in s:
        subject = 'a large simplified map with one highlighted location and one relevant business object'
    if 'walk' in s or 'left' in s or 'away' in s:
        action = 'walks away from the old institution toward the single new opportunity'
        emotion = 'quietly determined and liberated'
    elif 'sign' in s or 'signed' in s or 'check' in s:
        action = 'signs one decisive document while the surrounding risk remains visible'
        emotion = 'calm under pressure and defiant'
    elif 'inspect' in s or 'study' in s or 'research' in s or 'learn' in s:
        action = 'inspects one clearly visible component or document with intense concentration'
        emotion = 'disciplined and intensely focused'
    elif 'build' in s or 'make' in s or 'assemble' in s or 'produce' in s:
        action = 'assembles one visible component at a repeated workstation'
        emotion = 'methodical and ambitious'
    elif 'beat' in s or 'surpass' in s or 'dominate' in s or 'leading' in s:
        action = 'moves decisively above the rival marker on one oversized comparison graphic'
        emotion = 'surprising, triumphant, and undeniable'
    elif 'wait' in s or 'delay' in s or 'months' in s:
        action = 'waits beside an oversized frozen calendar and stacked review folders'
        emotion = 'frustrated and trapped by bureaucracy'
    elif 'fire' in s or 'ignite' in s or 'explod' in s or 'panic' in s:
        action = 'faces one contained red danger point while the main object remains clearly readable'
        emotion = 'tense and cautionary'
    if any(x in s for x in ['million','percent','months','years','first time','three straight','half a million','eleven thousand','forty-two thousand','twenty-eight','eight point six','ten days','five million','six months','three hundred thousand','two hundred thirty']):
        shot = 'diagram / infographic shot'
        subject = 'one oversized, clearly readable comparison graphic directly representing the narrated number or time period'
        action = 'reveals the decisive figure with one slow rising, falling, or counting motion'
        emotion = 'urgent, consequential, and easy to read'
        setting = 'Foreground: a cropped oversized number or chart edge; midground: the main graphic; background: two simple muted bands with a clean left-to-right reading order.'
        hook = 'The viewer sees the large number or comparison first, then the cause-and-effect relationship.'
    if any(x in s for x in ['said', 'asked', 'challenged', 'replied', 'who will', 'i will', 'sue me', 'question echoed']):
        shot = 'quote or dialogue shot'
        subject = 'the peach-faced main speaker with a consistent hairstyle and dark outfit'
        action = 'speaks while making one unmistakable hand gesture toward the listener'
        emotion = 'defiant, calm, or reassuring as required by the line'
        setting = 'Foreground: a large cropped listener frames one edge; midground: the speaker; background: a repeated office or meeting-room pattern.'
        hook = 'The complete handwritten quote in a large white cloud bubble is seen first, then the speaker’s expression.'
    if any(x in s for x in ['workers','engineers','executives','customers','families','critics','competitors','banks','rivals','team','committee']):
        shot = 'crowd / meeting shot'
        subject = 'the peach-faced detailed main character, clearly separated from white-faced repeated crowd figures'
        action = 'directs, inspects, or reacts with one readable gesture'
        emotion = 'the main character is focused while the white-faced crowd remains subdued and consistent'
        setting = 'Foreground: two oversized white-faced crowd figures are cropped at the edges; midground: the peach-faced lead; background: repeated desks, screens, stations, or vehicles.'
        hook = 'The viewer sees the unique peach-faced lead first, then the scale and reaction of the white-faced crowd.'
    # Fill remaining narration-specific defaults before the object pass.
    if subject == 'the story subject':
        if 'alone' in s or 'emptiness' in s or 'orphan' in s:
            subject = wang_child
            action = 'stands alone beside an empty bowl and a blank horizon'
            emotion = 'isolated but quietly resilient'
        elif 'poverty' in s or 'starved' in s or 'poor' in s:
            subject = 'young Wang beside a dry field and an empty bowl'
            action = 'looks across failed crops toward a distant village'
            emotion = 'vulnerable and observant'
        elif 'scholarship' in s or 'school' in s or 'stud' in s or 'formula' in s:
            subject = wang_child
            action = 'turns one open textbook beneath a small desk lamp'
            emotion = 'absorbed and relentlessly focused'
        elif 'mind' in s or 'brain' in s or 'weapon' in s:
            subject = wang_child
            action = 'holds an open book while a single red idea spark rises above it'
            emotion = 'resourceful and determined'
        elif 'call' in s or 'phone' in s:
            subject = 'Wang Chuan-fu and Lu Shang-yang as two simple stick figures connected by one old telephone line'
            action = 'Wang grips the receiver while Lu listens on the other side'
            emotion = 'secretive and high-stakes'
        elif 'factory' in s or 'company' in s or 'researcher' in s or 'bureaucracy' in s:
            subject = wang
            action = 'faces one oversized institutional object that blocks his next move'
            emotion = 'frustrated but strategically alert'
        else:
            subject = wang
            action = 'faces the single opportunity or obstacle named in the narration'
            emotion = 'quietly determined and alert'
    if any(x in s for x in ['battery','cell','blade','car','factory','mask','motor','supply chain','production line']):
        if 'battery' in s or 'cell' in s or 'blade' in s:
            subject = 'an oversized battery cell or Blade battery pack with a clear silhouette and only three internal details'
            action = 'changes state once—assembling, connecting, filling, resisting impact, or moving forward'
        elif 'factory' in s or 'production' in s or 'workers' in s:
            subject = 'a compact factory production line with one clearly highlighted station and repeated worker figures'
            action = 'assembles one visible component at a repeated workstation'
        elif 'car' in s or 'vehicle' in s:
            subject = 'one oversized compact electric car with a clear battery or production cue'
            action = 'moves forward once along a simple readable route'
    # Keep narration as semantic guidance without asking the generator to render long text.
    clean = re.sub(r'\s+', ' ', n).strip()
    return (f'{style} Shot type: {shot}. Main subject: {subject}. Action: {action}. Emotion: {emotion}. '
            f'{setting} Visual storytelling: show the concrete business consequence described in the narration—"{clean}"—using one large symbolic object and a strong size or state contrast, without adding unsupported events. '
            f'{hook} On-screen text: only short, large, correctly spelled labels if essential; do not render the full narration. {accent}. '
            'Video motion: first 30% holds the composition, middle 40% uses only one slow visible movement, final 30% holds a stable readable frame; static camera. '
            f'{negative}')

pattern = re.compile(r'(?ms)^\[SCENE (\d{3})\] \(([^\n]+)\)\nNarration: (.*?)\nVisual Prompt: .*?(?=\n----------------------------------------|\Z)')
scenes = list(pattern.finditer(text))
if len(scenes) != 169:
    raise SystemExit(f'Expected 169 scenes, found {len(scenes)}')
parts = []
for m in scenes:
    number, timecode, narration = m.group(1), m.group(2), m.group(3).strip()
    parts.append(f'[SCENE {number}] ({timecode})\nNarration: {narration}\nVisual Prompt: {prompt(narration, int(number))}\n----------------------------------------')
out.write_text('# Rewritten Visual Prompts\n\n' + '\n'.join(parts) + '\n', encoding='utf-8')
print(f'Wrote {len(scenes)} scenes to {out}')
