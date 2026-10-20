"""
Build granular ~10-15s script segmentation and complete Gemini Argon 4.0 video generation prompts.
Reports real-time percentage progress.
"""
import json
import re
import shutil
import sys
from pathlib import Path

# Force UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

INPUT_JSON = Path("data/history_of_microsoft/script_full_slow_paced.json")
OUTPUT_DIR_LOCAL = Path("data/history_of_microsoft")
OUTPUT_DIR_DRIVE = Path("G:/My Drive/Video_Projects/downloads/history_of_microsoft")

def get_shot_visual_direction(shot_idx, total_shots, ch_num, ch_title, text_en):
    t_lower = text_en.lower()
    
    # Contextual rules for high visual fidelity
    if "laptop" in t_lower or "coffee" in t_lower:
        desc = "Modern morning office, coffee steam, sleek laptop waking up with Start menu"
        prompt = "Cinematic 4K, slow smooth dolly push-in toward a sleek modern laptop on a dark wooden office desk in early morning light. A steaming ceramic coffee mug sits beside it. The laptop screen awakens, casting a soft blue glow showing a clean desktop interface with an incoming meeting notification. Atmospheric dust motes floating in warm window sunlight, shallow depth of field, 35mm lens, photorealistic, professional documentary cinematography, silent, no audio."
    elif "inbox" in t_lower or "spreadsheet" in t_lower or "meeting" in t_lower:
        desc = "Close-up of bustling digital office workflow, email alerts, Excel formulas"
        prompt = "Cinematic medium close-up of a high-resolution office monitor displaying complex financial spreadsheets and incoming email notification popups. An office worker's hands type smoothly on an ergonomic mechanical keyboard. Soft corporate ambient lighting, anamorphic lens flare, shallow focus on glowing interface, silent, no audio."
    elif "magic trick" in t_lower or "wallpaper" in t_lower or "routine" in t_lower:
        desc = "Conceptual visual of digital routine seamlessly woven into modern glass skyscrapers"
        prompt = "Cinematic wide panning shot of a sleek glass skyscraper corporate office floor at dusk. Dozens of glowing monitors illuminate professional workstations in unison. Symmetrical architectural framing, cool corporate cyan and amber color grade, smooth gimbal motion, high production value documentary B-roll, silent, no audio."
    elif "graveyard" in t_lower or "netscape" in t_lower or "wordperfect" in t_lower or "blackberry" in t_lower:
        desc = "Atmospheric museum archive of extinct tech giants, vintage boxes and hardware"
        prompt = "Dramatic high-contrast cinematic tracking shot across a shadowy museum archive shelf filled with dusty vintage technology relics: an old Netscape Navigator boxed software, a Lotus 1-2-3 manual, obsolete BlackBerry smartphones, and vintage CRT monitors with blank glass. Dramatic side-lighting, chiaroscuro atmosphere, slow tracking dolly, 35mm film grain, silent, no audio."
    elif "tea" in t_lower or "unfazed" in t_lower or "standing here" in t_lower:
        desc = "Microsoft modern headquarters campus in Redmond Washington under moody Pacific Northwest skies"
        prompt = "Cinematic sweeping aerial drone shot over the sprawling wooded Microsoft Redmond campus in Washington state. Modern glass and steel campus buildings nestled among evergreen pine trees under moody Pacific Northwest mist. Smooth cinematic crane descent, 4K UHD, photorealistic landscape, silent, no audio."
    elif "dusty" in t_lower or "new mexico" in t_lower or "strip mall" in t_lower:
        desc = "Arid Albuquerque New Mexico street in 1975, modest strip mall office"
        prompt = "Vintage 1975 period piece aesthetic, 16mm warm Kodachrome film look. An exterior view of a modest stucco strip mall office in Albuquerque, New Mexico under a blazing desert sun. Vintage 1970s cars parked on the dusty asphalt, retro hand-painted business signs, nostalgic American southwest documentary footage, silent, no audio."
    elif "nine hundred million" in t_lower or "scale" in t_lower:
        desc = "Abstract cinematic representation of global corporate financial scale and digital streams"
        prompt = "Cinematic visualization of global digital financial data streams pulsing across an illuminated 3D digital globe in a dark executive command room. Deep blue and gold light rays, holographic financial telemetry, slow cinematic rotation, ultra-sharp 8K render style, silent, no audio."
    elif "two nerdy teenagers" in t_lower or "dragged into" in t_lower or "antitrust" in t_lower:
        desc = "Split conceptual contrast: young computer nerds vs imposing courthouse pillars"
        prompt = "Cinematic juxtaposition: low-angle dramatic shot of grand federal courthouse limestone columns under brooding grey skies, transitioning smoothly to a vintage photo of two geeky teenage boys smiling in retro glasses. Moody lighting, high dynamic range, powerful historical documentary storytelling, silent, no audio."
    elif "data centers" in t_lower or "desert" in t_lower:
        desc = "Colossal hyperscale data center facility in the desert at twilight"
        prompt = "Breathtaking wide cinematic twilight shot of an immense, monolithic modern data center compound in the Arizona desert. Glowing perimeter security lights, cooling infrastructure arrays, subtle heat haze against a purple desert sunset horizon. Slow steady drone tracking shot, photorealistic 4K, silent, no audio."
    elif "rummage sale" in t_lower or "bake sale" in t_lower or "mothers club" in t_lower or "lakeside" in t_lower:
        desc = "Lakeside School 1968 rummage sale courtyard, vintage tables and baked goods"
        prompt = "Authentic vintage 1968 aesthetic, 16mm warm Kodachrome film stock. Suburban Seattle school courtyard, ladies in retro 1960s dresses and floral patterns selling homemade baked goods, vintage lamps, and books at a sunny charity rummage sale table. Handheld vintage camera drift, nostalgic golden hour sunlight, soft vintage color grade, silent, no audio."
    elif "teletype" in t_lower or "terminal" in t_lower or "paper tape" in t_lower:
        desc = "Teletype Model 33 terminal punching paper tape under a desk lamp"
        prompt = "Extreme macro vintage close-up of a mechanical Teletype Model 33 terminal print head clattering on yellow paper tape, spools spinning and punching holes furiously under the warm glow of a tungsten desk lamp. Heavy mechanical vibrations, authentic 1960s computing atmosphere, shallow depth of field, silent, no audio."
    elif "hooked" in t_lower or "paul allen" in t_lower or "bill gates" in t_lower or "burned through" in t_lower:
        desc = "Teen Bill Gates and Paul Allen huddled over computer terminal at night"
        prompt = "Atmospheric 1969 night shot inside a darkened school computer lab. A young skinny teenage Bill Gates with messy hair and thick glasses huddles beside tall teenage Paul Allen in flannel, watching glowing typed printouts with intense curiosity. Blue and amber rim lighting, cinematic 35mm grain, silent, no audio."
    elif "hacked" in t_lower or "billing" in t_lower or "banned" in t_lower:
        desc = "Close-up of young hands typing machine instructions on a glowing vintage mainframe console"
        prompt = "Cinematic close-up of teenage hands typing rapid command codes into a vintage terminal keyboard. Reflected green phosphor monitor text illuminates the boy's eyeglasses in a dark room. Tense covert hacker atmosphere, subtle handheld camera movement, silent, no audio."
    elif "night" in t_lower or "bugs" in t_lower or "unpaid" in t_lower:
        desc = "Late night empty corporate computer room, mainframe tapes spinning"
        prompt = "Cinematic 1970 night interior of a commercial computer center. Towering reel-to-reel magnetic tape drives slowly spinning in synchronization. Fluorescent ceiling lights dimmed, deep shadows, solitary young programmers working at distant desks. Atmospheric neo-retro cinematography, silent, no audio."
    elif "girls" in t_lower or "scheduling" in t_lower or "algorithm" in t_lower:
        desc = "1970s Lakeside high school hallway, lockers, and classroom scheduling punch cards"
        prompt = "Vintage 1971 high school corridor, students in bell-bottom trousers and retro sweaters chatting by metal lockers. A young student with thick glasses carries a stack of cardboard computer punch cards, looking subtly across the hall with an amused smile. Nostalgic 70s color palette, natural window daylight, silent, no audio."
    elif "traf-o-data" in t_lower or "traffic" in t_lower:
        desc = "1972 city street with black pneumatic rubber traffic counter hose across asphalt"
        prompt = "Vintage 1972 street scene in Seattle. Cars with retro chrome bumpers driving over a black pneumatic rubber traffic counter hose stretched across the asphalt. A small metal recording box chained to a nearby wooden telephone pole. Rainy Pacific Northwest street atmosphere, vintage 35mm documentary look, silent, no audio."
    elif "harvard" in t_lower or "popular electronics" in t_lower or "altair" in t_lower:
        desc = "Snowy Harvard Square newsstand 1974, Popular Electronics Altair 8800 cover"
        prompt = "Cinematic close-up on a vintage outdoor newsstand in snowy Harvard Square, Boston, winter 1974. Snowflakes gently falling. A young man's gloved hand reaches for a magazine with the bold headline: 'Popular Electronics - Project Breakthrough! World’s First Minicomputer Kit: Altair 8800'. Cinematic 35mm, cold winter atmosphere, slow camera tilt, silent, no audio."
    elif "ed roberts" in t_lower or "mits" in t_lower or "bluff" in t_lower:
        desc = "Vintage rotary telephone call in a college dorm room, tense negotiation"
        prompt = "Vintage 1975 college dormitory room. A young Bill Gates sits on an unmade bed, holding a black rotary telephone handset tightly to his ear, passionately pitching to a client while surrounded by stacks of computer printouts and manuals. Warm tungsten lighting, moody shadows, cinematic docudrama tone, silent, no audio."
    elif "simulator" in t_lower or "caffeine" in t_lower or "eight weeks" in t_lower:
        desc = "Exhausted all-night programming session with empty coffee mugs and code papers"
        prompt = "Late night Harvard computer lab, winter 1975. Stacks of green-bar perforated computer paper, empty porcelain coffee cups, cigarette smoke curling softly in warm lamp light. A tired young programmer rubbing his temples while staring into a glowing monitor screen. Gritty cinematic film grain, intense focus, silent, no audio."
    elif "airplane" in t_lower or "pencil" in t_lower or "turbulence" in t_lower or "bootstrap" in t_lower:
        desc = "Paul Allen on vintage passenger airliner handwriting machine code on notepad"
        prompt = "Inside a vintage 1975 commercial passenger airplane cabin, gentle camera turbulence. A young programmer sitting by an oval window overlooking the arid New Mexico desert below, furiously scribbling hexadecimal machine code with a yellow pencil onto a yellow legal notepad. Sweat on his brow, intense ticking-clock suspense, documentary lighting, silent, no audio."
    elif "memory size" in t_lower or "worked" in t_lower:
        desc = "Altair 8800 LED panel blinking, Teletype stamping 'MEMORY SIZE?'"
        prompt = "Extreme macro shot in a cluttered 1970s Albuquerque electronics workshop. A vintage metal Altair 8800 computer box with rows of red and green toggle switches and blinking indicator LEDs. An adjacent teletype print head stamps ink into paper: 'MEMORY SIZE?'. Dramatic low-key lighting, retro-tech documentary masterpiece, silent, no audio."
    elif "drop out" in t_lower or "founded" in t_lower or "april 4" in t_lower:
        desc = "Albuquerque office exterior with early 1970s 'Micro-Soft' door lettering"
        prompt = "Sun-drenched 1975 Albuquerque office exterior. A frosted glass door with simple retro black vinyl lettering reading 'Micro-Soft'. Warm desert breeze blowing dry leaves across the walkway. Golden 1970s Kodachrome aesthetic, slow cinematic push-in toward the door, silent, no audio."
    elif "homebrew" in t_lower or "hobbyists" in t_lower or "open letter" in t_lower or "stealing" in t_lower:
        desc = "1976 Homebrew Computer Club gathering in garage, sharing paper tapes"
        prompt = "Vintage 1976 garage meeting of passionate computer hobbyists in Menlo Park. Young men in denim jackets, flannel shirts, and unkempt beards gathered around wooden workbenches, enthusiastically swapping spools of punched paper tape. Warm incandescent garage lighting, authentic counterculture tech atmosphere, silent, no audio."
    elif "mugshot" in t_lower or "arrested" in t_lower or "albuquerque police" in t_lower:
        desc = "Iconic 1977 police booking height chart with retro camera flash"
        prompt = "Vintage 1977 police station booking room. A height measurement chart on the wall. A young man in retro eyeglasses and an open floral collar shirt smiling broadly with boyish charm as a bright flashbulb pops. Authentic 70s police archival photograph look, monochrome to vintage color transition, silent, no audio."
    elif "ibm" in t_lower or "blue suits" in t_lower or "suits" in t_lower:
        desc = "Imposing IBM corporate headquarters, executives in dark blue suits"
        prompt = "Cinematic slow-motion low-angle tracking shot of three rigid IBM executives in immaculate dark blue wool suits, white shirts, dark ties, and polished wingtip shoes carrying leather briefcases down a corporate corridor. Stern corporate aura, symmetrical Kubrick-style framing, cool sterile lighting, silent, no audio."
    elif "kildall" in t_lower or "digital research" in t_lower or "airplane" in t_lower:
        desc = "Small private propeller airplane sitting on sunny California airfield runway"
        prompt = "A vintage single-engine Cessna propeller airplane parked on a sunlit tarmac in Pacific Grove, California in 1980. Coastal fog rolling over pine trees in the distance. The propeller glints in the sunlight. Cinematic retro documentary establishing shot, silent, no audio."
    elif "qdos" in t_lower or "75,000" in t_lower or "paterson" in t_lower:
        desc = "Tim Paterson inspecting 8-inch floppy disks labeled 'QDOS' on cluttered desk"
        prompt = "Vintage 1980 Seattle rainy day. Inside a modest, dimly lit independent computer repair shop, an engineer in a casual flannel shirt inspects an 8-inch black floppy disk containing the handwritten label 'QDOS 0.1'. A paper contract on the desk under tungsten desk lamp. Cinematic rain droplets on window, silent, no audio."
    elif "license" in t_lower or "non-exclusive" in t_lower or "contract" in t_lower:
        desc = "Pen signing legal licensing contract under warm executive desk lamp"
        prompt = "Extreme macro cinematic close-up of a vintage fountain pen signing the signature line of a legal software licensing contract. Crisp legal paper texture, dark ink soaking into the fibers. Soft warm executive desk lighting, cinematic shallow focus, silent, no audio."
    elif "clone" in t_lower or "compaq" in t_lower or "pc" in t_lower:
        desc = "1980s PC assembly factory floor, rows of beige computers being built"
        prompt = "1982 computer manufacturing assembly line. Factory workers in blue lab coats assembling beige desktop computers with printed circuit boards, ribbon cables, and floppy disk drives. Clean industrial lighting, smooth tracking camera glide, period documentary realism, silent, no audio."
    elif "640k" in t_lower or "memory" in t_lower or "ram" in t_lower:
        desc = "Macro close-up of vintage 1980s DIP RAM chips on green motherboard"
        prompt = "Extreme macro focus pull across vintage ceramic dual-in-line memory chips on a green computer motherboard from 1981. Tiny copper traces and solder joints glint under precision cinematic lighting. Ultra-detailed electronic texture, silent, no audio."
    elif "floorboards" in t_lower or "foundation" in t_lower or "standard" in t_lower:
        desc = "Conceptual visual of massive industrial architectural foundations"
        prompt = "Cinematic conceptual shot: deep underground steel and concrete structural foundations of a modern skyscraper, glowing with pulsating digital light conduits. Symmetrical low-angle perspective, powerful visual metaphor for digital platform dominance, moody atmospheric lighting, silent, no audio."
    elif "windows 1.0" in t_lower or "clunky" in t_lower or "1985" in t_lower:
        desc = "Windows 1.0 running on beige CRT monitor with blue tiled interface"
        prompt = "A vintage beige CRT monitor in 1985 displaying the primitive tiled graphical interface of Microsoft Windows 1.0. A boxy beige two-button computer mouse rests on a foam pad next to the keyboard. Authentic scan lines, warm home-office ambience, slow cinematic pan, silent, no audio."
    elif "excel" in t_lower or "macintosh" in t_lower or "apple" in t_lower and "1985" in t_lower:
        desc = "Vintage 1984 beige Apple Macintosh computer running Microsoft Excel 1.0"
        prompt = "Iconic vintage beige Apple Macintosh 128K computer sitting on a wooden desk in 1985. The tiny 9-inch crisp black and white screen displays Microsoft Excel spreadsheet grids with bar charts. Soft morning window light, high nostalgic tech authenticity, silent, no audio."
    elif "ipo" in t_lower or "billionaire" in t_lower or "stock" in t_lower or "21 dollars" in t_lower:
        desc = "Wall Street 1986 trading floor and NASDAQ ticker displaying MSFT"
        prompt = "Vintage 1986 NASDAQ stock exchange trading floor. Wall Street brokers in retro suits and glasses shouting and waving paper order slips under glowing amber electronic stock ticker boards flashing 'MSFT 21.00'. Energetic period docudrama camera move, film grain, silent, no audio."
    elif "lawsuit" in t_lower or "xerox" in t_lower or "stole" in t_lower:
        desc = "Courthouse steps and Xerox PARC California research facility exterior"
        prompt = "1980s Palo Alto California corporate research park exterior, modernist concrete and glass building surrounded by eucalyptus trees in bright California sunlight. Smooth tracking shot, cinematic documentary atmosphere, silent, no audio."
    elif "windows 95" in t_lower or "start me up" in t_lower or "rolling stones" in t_lower:
        desc = "Windows 95 colorful box packaging and midnight store crowd cheering"
        prompt = "August 1995, midnight outside a brightly lit computer superstore. A massive queue of excited retro-styled customers in 90s windbreakers and jackets waiting in the night. Neon signs reflect on the wet pavement. Store doors open and employees begin handing out iconic colorful Windows 95 boxes featuring rolling clouds and blue skies. Energetic documentary camera glide, silent, no audio."
    elif "empire state" in t_lower or "manhattan" in t_lower:
        desc = "Empire State Building illuminated in Windows quad-colors at night"
        prompt = "Spectacular cinematic aerial night drone shot of the Manhattan skyline in 1995. The iconic Empire State Building illuminated in brilliant Windows logo colors: glowing red, green, blue, and yellow against the dark midnight sky. Misty clouds drift past the skyscraper spire, anamorphic lens flares, breathtaking cinematic scale, silent, no audio."
    elif "printer" in t_lower or "freezing" in t_lower or "dark" in t_lower:
        desc = "1990s noisy dot-matrix printer feeding continuous tractor paper"
        prompt = "Close-up of a vintage 1995 dot-matrix printer furiously printing black text onto perforated fanfold paper, the paper feeding smoothly upward with rhythmic mechanical precision. Cozy 90s office setting, soft lamplight, nostalgic office tech realism, silent, no audio."
    elif "internet tidal wave" in t_lower or "memo" in t_lower:
        desc = "Bill Gates 1995 memo paper titled 'The Internet Tidal Wave' on mahogany desk"
        prompt = "Cinematic slow push-in on a typed paper memo lying on an executive desk under a focused reading light, with bold header: 'The Internet Tidal Wave - Bill Gates, May 26, 1995'. A dark ocean wave painting on the office wall in the soft background blur. Dramatic corporate intrigue atmosphere, silent, no audio."
    elif "netscape" in t_lower or "internet explorer" in t_lower or "browser war" in t_lower:
        desc = "1990s desktop monitor comparing Netscape Navigator ship helm logo and IE blue 'e' logo"
        prompt = "A high-end 1996 CRT computer monitor with dual browser windows open side-by-side: Netscape Navigator with its iconic glowing ship helm wheel, and Internet Explorer with the blue 'e' logo. Dynamic split-focus camera pan, reflections on curved CRT glass, silent, no audio."
    elif "deposition" in t_lower or "doj" in t_lower or "diet coke" in t_lower:
        desc = "Bill Gates 1998 antitrust videotaped deposition TV monitor in federal court"
        prompt = "Washington D.C. 1998 federal courtroom. Heavy dark mahogany wood walls, stacks of legal documents. A television monitor shows a young corporate executive in oversized glasses rocking slowly back and forth during a tense videotaped deposition. Cold legal drama lighting, slow dramatic camera push-in, silent, no audio."
    elif "clippy" in t_lower or "paperclip" in t_lower or "letter" in t_lower:
        desc = "Animated Clippy assistant tapping on glass inside Microsoft Word 97"
        prompt = "Close-up of a vintage beige CRT computer monitor running Microsoft Word 97. In the bottom right corner, the infamous 3D animated paperclip assistant with big googly eyes taps on the glass from inside the screen with a speech bubble: 'It looks like you’re writing a letter!'. Warm nostalgic room lighting, authentic screen scanlines, humorous subtle camera zoom, silent, no audio."
    elif "ballmer" in t_lower or "developers" in t_lower or "sweat" in t_lower:
        desc = "Steve Ballmer screaming DEVELOPERS energetically on conference stage"
        prompt = "A bustling 2000s tech developer conference arena. A dynamic CEO figure in a blue dress shirt soaked in perspiration pacing enthusiastically across a massive illuminated stage, gesturing wildly with high energy toward a giant screen displaying 'DEVELOPERS'. Bright stage spotlights, stadium camera tracking, high-energy documentary footage, silent, no audio."
    elif "xbox" in t_lower or "red ring" in t_lower:
        desc = "Xbox 360 console in dark living room flashing three red lights of death"
        prompt = "Moody cinematic close-up of a matte white Xbox 360 video game console resting horizontally in a dark entertainment center. The power button ring flashes with the ominous three glowing red quadrant lights of the 'Red Ring of Death'. Eerie red glow casting dramatic shadows across the carpet, slow cinematic dolly back, silent, no audio."
    elif "iphone" in t_lower or "keyboard" in t_lower or "five hundred" in t_lower:
        desc = "2007 original iPhone sleek glass screen rotating elegantly"
        prompt = "Pristine product commercial cinematography: the original 2007 aluminum and black plastic iPhone rotating slowly in mid-air under dramatic studio rim lights. The vibrant capacitive glass touchscreen illuminates with colorful app icons against a dark minimalist studio backdrop. Smooth cinematic 360-degree rotation, 4K UHD, silent, no audio."
    elif "nokia" in t_lower or "vista" in t_lower or "write off" in t_lower:
        desc = "Lumia colorful Windows Phones arranged on clean display pedestal"
        prompt = "Sleek display table featuring bright yellow, cyan, and red Nokia Lumia Windows Phone smartphones with dynamic live tiles glowing on screens. Modern museum-like lighting, slow tracking dolly shot, vibrant product design aesthetic, silent, no audio."
    elif "nadella" in t_lower or "satya" in t_lower or "learn-it-all" in t_lower:
        desc = "Satya Nadella keynote address in contemporary auditorium"
        prompt = "Modern Redmond campus auditorium, bright airy contemporary architecture. A calm, intellectual tech leader in modern dark casual attire standing before a vast LED presentation screen projecting the bold minimalist statement: 'Microsoft Loves Linux'. Warm natural lighting, clean depth of field, sophisticated modern documentary style, silent, no audio."
    elif "minecraft" in t_lower or "github" in t_lower or "linkedin" in t_lower:
        desc = "Collage visual of Minecraft voxel world, GitHub Octocat, and modern software code"
        prompt = "Cinematic macro shot of software engineer writing code in Visual Studio Code on dual 4K monitors. A small 3D glowing Minecraft voxel block and an Octocat mascot figurine sit beside the keyboard. Ambient purple and cyan neon desk backlighting, high-end tech workspace aesthetic, silent, no audio."
    elif "pandemic" in t_lower or "teams" in t_lower or "activision" in t_lower:
        desc = "Modern home office video conference call mosaic of smiling remote workers"
        prompt = "Cozy modern apartment home office during sunset. A worker participates in a Microsoft Teams video conference call displayed on an ultrawide monitor, showing a 3x3 grid of colleagues collaborating from different home environments worldwide. Warm domestic lighting, authentic modern work culture realism, silent, no audio."
    elif "openai" in t_lower or "sam altman" in t_lower or "48 hours" in t_lower:
        desc = "Late night boardroom high above rainy city during OpenAI weekend negotiations"
        prompt = "High-floor corner boardroom in Seattle overlooking city lights on a rainy November midnight. Multiple laptops open showing breaking tech news tweets and emergency video conference calls. Empty coffee cups, intense high-stakes negotiations atmosphere, cinematic moody reflections on glass, corporate thriller tone, silent, no audio."
    elif "azure" in t_lower or "server" in t_lower or "cloud" in t_lower:
        desc = "Immense Azure hyperscale cloud data center server aisle with pulsing blue fiber optics"
        prompt = "Breathtaking cinematic tracking shot inside a colossal hyperscale data center facility. Infinite rows of server racks stretching into the distance with thousands of pulsing blue and green fiber-optic LED lights. Cold blue atmospheric mist, symmetrical one-point perspective, sleek high-tech corporate infrastructure, slow smooth glide, silent, no audio."
    elif "copilot" in t_lower or "ai assistant" in t_lower:
        desc = "Futuristic yet realistic workplace with holographic Copilot AI assistance beside user"
        prompt = "Ultra-modern minimalist workstation in a glass-walled penthouse office at sunrise. A user sits in front of an ultra-wide curved OLED monitor where intelligent AI assistant prompts seamlessly generate data graphs, email responses, and visual dashboards. Cinematic slow pan, golden morning light streaming through floor-to-ceiling windows, photorealistic masterpiece, silent, no audio."
    elif "nuclear" in t_lower or "cooling" in t_lower or "chips" in t_lower or "nvidia" in t_lower:
        desc = "Modern advanced nuclear power cooling facility powering computing infrastructure"
        prompt = "Epic aerial cinematic tracking shot of clean modern energy infrastructure at dawn: cooling towers and green power substations dedicated to powering a nearby mega-scale computing facility. Misty morning atmosphere, soft sunbeams piercing clouds, epic industrial engineering scale, silent, no audio."
    elif "subscribe" in t_lower or "comments" in t_lower or "next build" in t_lower or "see you" in t_lower:
        desc = "Cinematic documentary outro screen, minimalist YouTube subscribe and comment cards"
        prompt = "Sleek, minimalist dark-mode motion graphics outro sequence: subtle floating 3D glass typography cards with 'Subscribe' and 'Comment' iconography over a smooth animated gradient of deep cobalt blue and slate gray. Polished cinematic finish, high-end YouTube documentary post-production aesthetic, silent, no audio."
    else:
        desc = f"Documentary B-roll scene representing {ch_title}"
        prompt = f"Cinematic 4K documentary B-roll shot representing {ch_title}. High-end cinematic camera tracking, anamorphic lens, beautiful atmospheric documentary lighting, 35mm film grain, ultra-sharp realistic textures, professional color grading, silent, no audio."

    return desc, prompt

def main():
    print("=== BUILDING GRANULAR ~10-15s SCRIPT & GEMINI ARGON 4.0 PROMPTS ===")
    cues = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    total_input_cues = len(cues)
    print(f"Loaded {total_input_cues} cues from {INPUT_JSON.name}")

    # Segment each cue into balanced ~11.5s shots
    raw_shots = []
    for c in cues:
        dur = c["duration"]
        t_start = c["start"]
        t_end = c["end"]
        n_shots = max(1, round(dur / 11.5))

        words_en = c["text_en"].split()
        words_vi = c["text_vi"].split()

        step_en = len(words_en) / n_shots
        step_vi = len(words_vi) / n_shots
        step_dur = dur / n_shots

        for k in range(n_shots):
            s_en = " ".join(words_en[int(k * step_en): int((k + 1) * step_en)])
            s_vi = " ".join(words_vi[int(k * step_vi): int((k + 1) * step_vi)])
            s_start = round(t_start + k * step_dur, 2)
            s_end = round(t_start + (k + 1) * step_dur, 2) if k < n_shots - 1 else t_end
            raw_shots.append({
                "chapter_num": c["chapter_num"],
                "chapter_title": c["chapter_title"],
                "start": s_start,
                "end": s_end,
                "duration": round(s_end - s_start, 2),
                "text_en": s_en,
                "text_vi": s_vi
            })

    total_shots = len(raw_shots)
    print(f"Generated {total_shots} granular shots (Target: 10-15s per clip, Silent / No Audio)")

    full_shots = []
    for i, s in enumerate(raw_shots):
        shot_id = f"SHOT_{i+1:03d}"
        desc, prompt = get_shot_visual_direction(i, total_shots, s["chapter_num"], s["chapter_title"], s["text_en"])
        
        m1, s1 = int(s["start"] // 60), int(s["start"] % 60)
        m2, s2 = int(s["end"] // 60), int(s["end"] % 60)
        timecode = f"{m1:02d}:{s1:02d} - {m2:02d}:{s2:02d}"

        full_shots.append({
            "shot_id": shot_id,
            "index": i + 1,
            "chapter_num": s["chapter_num"],
            "chapter_title": s["chapter_title"],
            "start": s["start"],
            "end": s["end"],
            "duration": s["duration"],
            "timecode": timecode,
            "text_en": s["text_en"],
            "text_vi": s["text_vi"],
            "visual_description": desc,
            "gemini_argon_prompt": prompt
        })

        # Real-time percentage progress
        percent = ((i + 1) / total_shots) * 100
        if (i + 1) % 10 == 0 or i + 1 == total_shots:
            sys.stdout.write(f"\r[PROGRESS: {percent:5.1f}%] Processed {i+1:03d}/{total_shots} shots | Ch.{s['chapter_num']:02d}: {s['chapter_title'][:25]}")
            sys.stdout.flush()

    print("\n\nAll 133 shots processed! Now exporting files...")

    def format_srt_time(sec):
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    for target_dir in [OUTPUT_DIR_LOCAL, OUTPUT_DIR_DRIVE]:
        target_dir.mkdir(parents=True, exist_ok=True)

        # 1. JSON
        json_file = target_dir / "script_granular_10s_full.json"
        json_file.write_text(json.dumps(full_shots, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved: {json_file}")

        # 2. English SRT
        srt_en = target_dir / "script_granular_10s_en.srt"
        with open(srt_en, "w", encoding="utf-8") as f:
            for item in full_shots:
                f.write(f"{item['index']}\n")
                f.write(f"{format_srt_time(item['start'])} --> {format_srt_time(item['end'])}\n")
                f.write(f"{item['text_en']}\n\n")
        print(f"Saved: {srt_en}")

        # 3. Vietnamese SRT
        srt_vi = target_dir / "script_granular_10s_vi.srt"
        with open(srt_vi, "w", encoding="utf-8") as f:
            for item in full_shots:
                f.write(f"{item['index']}\n")
                f.write(f"{format_srt_time(item['start'])} --> {format_srt_time(item['end'])}\n")
                f.write(f"{item['text_vi']}\n\n")
        print(f"Saved: {srt_vi}")

        # 4. Text Full
        txt_file = target_dir / "script_granular_10s_full.txt"
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("=== THE ENTIRE HISTORY OF MICROSOFT IN 26 MINUTES (10-15s GRANULAR SHOTS) ===\n")
            f.write(f"Total Shots: {total_shots} | Granular Clip Duration: 10s - 15s | Audio: Silent / No Audio\n\n")
            curr_ch = 0
            for item in full_shots:
                if item["chapter_num"] != curr_ch:
                    curr_ch = item["chapter_num"]
                    f.write("=" * 70 + "\n")
                    f.write(f"CHAPTER {curr_ch}: {item['chapter_title'].upper()} [{item['timecode'].split(' - ')[0]}]\n")
                    f.write("=" * 70 + "\n\n")
                f.write(f"[{item['shot_id']}] [{item['timecode']}] (dur: {item['duration']}s)\n")
                f.write(f"EN: {item['text_en']}\n")
                f.write(f"VI: {item['text_vi']}\n")
                f.write(f"PROMPT: {item['gemini_argon_prompt']}\n\n")
        print(f"Saved: {txt_file}")

        # 5. Master Markdown Prompt File for Gemini Argon 4.0
        md_file = target_dir / "gemini_argon_master_prompt_full.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write("# MASTER PROMPT CHO GEMINI ARGON 4.0: TRỌN BỘ 133 CẢNH VIDEO B-ROLL (10-15S / CẢNH, KHÔNG AUDIO)\n\n")
            f.write("Dán toàn bộ tài liệu này vào Gemini Argon 4.0 để agent sinh video B-roll tài liệu điện ảnh 4K cho từng mốc thời gian.\n\n")
            f.write("```markdown\n")
            f.write("You are an expert Hollywood Documentary Cinematographer & AI Video Generation Director for 'The ENTIRE History of Microsoft in 26 Minutes'.\n")
            f.write("CONSTRAINTS:\n")
            f.write("1. DURATION: Exactly 10 to 15 seconds per shot.\n")
            f.write("2. AUDIO: NO AUDIO (Audio: false / Silent video footage only). The narration audio is produced separately.\n")
            f.write("3. QUALITY: 4K UHD, 16:9 widescreen, 24fps, cinematic 35mm film grain, zero digital distortions.\n\n")
            f.write("### COMPLETE SHOT-BY-SHOT PRODUCTION PROMPTS (133 SHOTS):\n\n")
            for item in full_shots:
                f.write(f"* **{item['shot_id']} [{item['timecode']}] (Duration: {item['duration']}s)**\n")
                f.write(f"  * *Script Line*: \"{item['text_en']}\"\n")
                f.write(f"  * *Prompt*: {item['gemini_argon_prompt']}\n\n")
            f.write("```\n")
        print(f"Saved: {md_file}")

    print("=== EXPORT SUCCESSFUL ===")

if __name__ == "__main__":
    main()
