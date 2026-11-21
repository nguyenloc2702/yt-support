# Crayon Explainer Generation (Mold Specification)

> **Tài liệu thuộc bộ Skill:** [`skills/shot_generation/`](SKILL.md)  
> **Khung sườn chi tiết:** Xem [`UNIVERSAL_SHOT_FRAMEWORK.md`](UNIVERSAL_SHOT_FRAMEWORK.md)  
> **Quy trình tổng thể:** Xem [`skills/video_production/`](../video_production/SKILL.md)

## When to use this skill

- Use this skill whenever the user asks for an explainer image or video shot in the locked Crayon Capital mold, or asks any later agent to generate in that mold.
- Use this skill before writing any image or video prompt for such a project.
- Do not use this skill as a general video production guide. It covers only the image and shot generation mold below.

## Lock before you generate

- Copy the Style Lock block below verbatim into every prompt. Do not rewrite it from memory.
- Copy the Character Sheet verbatim into every prompt that shows the main character. Do not redesign the character in a later shot.
- Lock the aspect ratio with the style. Horizontal video is 16:9. Vertical video is 9:16 only when the format requires it.
- If a tool returns the wrong aspect ratio, regenerate the shot. Do not crop a vertical shot into a horizontal frame to rescue it.
- Test all four shot types (wide, quote, crowd, diagram) before bulk generation. Fix the Style Lock or Character Sheet when a test fails. Do not fix a style failure by generating more variants at random.

```text
STYLE LOCKED:
Simple flat 2D cartoon explainer, clean sharp professional line art.
Crisp, smooth, bold black outlines, clean vector edges.
Flat colours, minimal shading: exactly one flat grey side shadow
on the shaded side of a head or body, no gradient, no detailed shading.
Characters are stick figures: oversized round heads, very small dot eyes,
simple curved eyebrows, wide toothy grins OR shocked open mouths,
a small oval or tube body, thin single-line stick arms and legs,
hands as small plain blobs, no fingers, no detailed clothing folds.
Main character: peach skin face, detailed hair, dark or coloured outfit,
sharp and high contrast. Crowd characters: plain white faces,
no skin tone, one flat grey side shadow, simple flat hair,
repeated identical oval bodies, lower contrast than the main character.
Muted-but-clear palette, slightly desaturated: dusty blue, muted navy,
muted red, off-white, slate grey, warm earth tones.
One accent colour per shot, muted red, reserved for the focal object,
number, or stamp. Everything else stays muted so the accent punches.
Bright, simple backgrounds, one clear scene, no clutter, still bright and readable.
Aspect ratio: 16:9, unless the format requires 9:16.
Negative: NO crayon texture, NO grain, NO paper texture,
NO rough sketchy wobbly lines, NO childish doodle texture,
NO overly vivid candy colours, NO dark grey muddy look,
no photorealism, no 3D render, no detailed shading, no gradient,
no blurry, no extra limbs, no distorted hands,
no crowded background, no tiny unreadable text, no garbled text,
no realistic torso, shirt, or jacket with folds and buttons,
no crowd character with the same skin tone and detail as the main character.
```

## Visualise the frame before you generate

- State where the eye looks first, then where it looks second.
- State what is in the frame and what is deliberately not in the frame.
- Keep one idea, one focal point, and one or two background objects per shot.
- Use scale for emphasis. Make the key subject very large, or place a tiny figure beside a huge building or rocket.
- Use repetition for scale and systems. Repeat identical desks, monitors, or cells instead of adding new details.
- Show abstract ideas with large simple text or numbers, a rising zigzag line, or a size contrast. Do not explain an abstract idea with a paragraph of on-screen text.
- Keep on-screen text to one to three words when it is truly needed. Use large capitals. Leave text out and add it in editing when it is not needed.

## Generate the still image

- Fill the five mandatory slots first: subject, action, focal point, background, and negative.
- Write what the tool will see, not what the user is thinking. Replace a feeling with a visible pose, expression, or object.
- Describe one frozen state. Do not describe a sequence of actions in an image prompt.
- Keep the background simple enough that it never competes with the foreground subject.
- Check the result in two seconds. If the main subject and action are not clear in two seconds, regenerate with a simpler background or a larger focal point.

## Generate the video shot

- Start from the approved keyframe image when the tool allows it.
- Use a hard cut as the main motion between ideas. Cut and reframe to a wide shot, a close-up, or a text card.
- Add at most one small, slow motion inside a shot. Examples are a blink, a glow that brightens, or a chart line that rises.
- Divide the duration by proportion, not by a fixed five second template. Use about 30 percent to open, 40 percent for the single main motion, and 30 percent to hold a stable final frame.
- End on a stable frame so the next hard cut does not feel like a jump in the middle of a motion.
- Do not turn a shot into continuous acting, a walk cycle, or a camera pan that pretends to be a new idea. Those motions were not verified in the reference video sampled on 2026-11-03.
- Do not use a pan or zoom as a substitute for a new shot. When the idea changes, cut to a new composition.

## Keep shots continuous

- Keep the same face, hairstyle, outfit, head to body proportion, palette, and lighting across shots in one project.
- Change only one group of slots at a time. Change the subject and setting for a new topic, or change expression and background tone for a new emotion, or change aspect ratio and shot size for a new format.
- Check each new shot against the previous shot for face, outfit, light, viewing direction, and aspect ratio.
- Reject a shot that changes the main character, even when that shot looks attractive on its own.

## Reject these variants

- Reject polished soft gradient vector art with detailed shading.
- Reject crayon texture, grain, paper texture, and rough sketchy lines.
- Reject overly vivid candy colours and reject a dark grey muddy look.
- Reject crowded backgrounds, extra limbs, distorted hands, and tiny unreadable text.

## Verified limits

- The reference is Crayon Capital, The ENTIRE History of Elon Musk, https://www.youtube.com/watch?v=npoPedEq3hs, 19:09, rechecked on 2026-11-03 without sign-in.
- The recheck sampled frames at intervals. It did not inspect every frame.
- Do not claim a verified pan, zoom, walk cycle, rocket explosion, factory detail, or launch cost chart from that recheck. Those were not in the sampled frames.
- Sponsor material in the reference is excluded from this mold.

## Related Skills & Integration

- Use with [`UNIVERSAL_SHOT_FRAMEWORK.md`](UNIVERSAL_SHOT_FRAMEWORK.md) for the complete 14-box framework, prompt formulas, camera motion vocabulary, and shot-chain checklist.
- Integrate with [`skills/video_production/SKILL.md`](../video_production/SKILL.md) for full scriptwriting, audio metrics, and 60-point QA review.
