export const BRIDGE_DIRECTIONS = [
  { id: "auto", label: "Follow source notes" },
  { id: "left", label: "Travel left" },
  { id: "right", label: "Travel right" },
  { id: "forward", label: "Push forward" },
  { id: "backward", label: "Pull back" },
] as const;

export interface BridgeDirectionOptions {
  energy?: "restrained" | "balanced" | "bold";
  direction?: typeof BRIDGE_DIRECTIONS[number]["id"];
  anchor?: string;
}

interface BridgePrompt {
  id: string;
  name: string;
  group: "cinematic" | "experimental";
  fit: string;
  check: string;
  text: string;
  directions: readonly NonNullable<BridgeDirectionOptions["direction"]>[];
}

// These are editable creative directions, not provider-native effect IDs or quality guarantees.
// Motion-first, positive phrasing and readable reveals adapted on 2026-09-16 from:
// https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide
// https://help.runwayml.com/hc/en-us/articles/47313504791059-Camera-Terms-Prompts-Examples
// https://help.runwayml.com/hc/en-us/articles/46182941379347-Introduction-to-Prompting
// Those guides demonstrate Gen-4.5; they do not verify these starters on the hosted bridge models.
export const BRIDGE_PROMPTS: readonly BridgePrompt[] = [
  { id: "whip", name: "Camera whip", group: "cinematic", directions: ["auto", "left", "right"],
    fit: "Choose similar camera heights, horizons and subject scales. Match the screen direction of movement and keep exposure close across the pair.",
    check: "Check direction and speed at both joins; watch for a reverse whip or a frozen landing.",
    text: "The camera connects the supplied first and last frames in one continuous lateral whip. Follow the screen direction in the motion notes, build speed into a brief sweep of directional blur, then reveal the destination as the blur clears. Ease into the final framing and continue the incoming subject's motion. Keep the subjects and scene lighting recognizable throughout the arrival." },
  { id: "occlusion", name: "Pass through", group: "cinematic", directions: ["auto", "left", "right", "forward"],
    fit: "Choose a nearby column, doorway edge or other opaque foreground cover, with a clear route past it. Let the destination continue the travel direction and lighting beyond that cover.",
    check: "Check that the cover moves in one direction and fully hides the scene change without stretching the subject.",
    text: "The camera connects the supplied first and last frames in one continuous move past the foreground surface described in the notes. This nearby cover briefly fills the entire view, concealing the scene change. Continue along the same route as its trailing edge reveals the destination, then ease into the final composition. Keep the endpoint subjects recognizable and let their motion continue naturally." },
  { id: "flight", name: "Camera flight", group: "cinematic", directions: ["auto", "forward", "backward"],
    fit: "Try an elevated city view into a street, or a route through an open doorway. Choose a visible path, compatible light and an achievable arrival angle; describe the height change and what comes into view.",
    check: "Check foreground parallax, scale and horizon through the journey. The camera should travel through open space and ease into the incoming camera height, rather than zooming a flat image or hitting the street.",
    text: "The camera travels from the supplied first frame to the last along the single open route described in the notes. Move through the space and follow the specified change in camera height, with nearby surfaces passing faster than distant ones. Build speed through the middle, bring the destination into view, then ease into its camera height, angle and final framing. Continue naturally into the incoming motion in one continuous shot." },
  { id: "focus", name: "Focus pull", group: "cinematic", directions: ["auto"],
    fit: "Choose similarly placed subjects at different focal distances, with compatible lighting. Quiet camera movement and aligned silhouettes make the focus change easier to read.",
    check: "Check that blur peaks briefly and the incoming subject resolves without a face or silhouette morph.",
    text: "In one continuous shot, shift focus from the subject in the supplied first frame toward the focal distance described for the last frame. Let a brief soft blur conceal the change around the middle, then bring the incoming subject gently into clear focus. Keep the camera steady, the subjects recognizable and their motion natural as the final composition resolves." },
  { id: "light", name: "Light bridge", group: "cinematic", directions: ["auto", "left", "right", "forward"],
    fit: "Choose an existing lamp, sun or bright highlight that the camera can move past. Similar light color, exposure and shadow direction help the destination emerge naturally.",
    check: "Check exposure recovery and skin detail; reject a sustained whiteout or a second flash at the exit.",
    text: "The camera connects the supplied first and last frames by moving past the existing light source described in the notes. Its highlights bloom briefly across the view around the middle while some shadow structure remains visible. Reveal the destination as the bloom recedes, then ease into the final framing with natural exposure and clear subject detail. Keep this one continuous move with a single light accent." },
  { id: "reflection", name: "Reflection passage", group: "cinematic", directions: ["auto", "left", "right", "forward"],
    fit: "Choose visible glass, a mirror or still water with a readable approach angle. Align the reflected scene's horizon, light and travel direction with the destination.",
    check: "Check reflected anatomy, travel direction and the final horizon; reject duplicated faces or a lingering glass layer.",
    text: "The camera connects the supplied first and last frames in one continuous approach to the reflection described in the notes. Let the reflected scene expand to fill the view around the middle, carrying the change into the destination. Keep the surface perspective readable during the approach, then let the reflection clear as the camera eases into the final composition. The endpoint subjects remain recognizable." },
  { id: "morph", name: "Match morph", group: "experimental", directions: ["auto"],
    fit: "Choose aligned silhouettes, similar subject scale and a shared visual anchor. Similar poses and light direction help; use different identities only for an intentional transformation.",
    check: "Check the shared anchor and anatomy throughout; different people require an intentional identity change.",
    text: "Create one deliberate transformation from the supplied first frame into the last. Keep the shared anchor aligned and the main silhouette readable as the form changes through the middle. Follow the identity-change instructions in the notes, preserving identity when both frames show the same subject. Complete the transformation with clear anatomy and texture, then ease into the incoming motion and final composition." },
  { id: "portal", name: "Aperture portal", group: "experimental", directions: ["auto", "forward", "backward"],
    fit: "Choose a doorway, circle or screen with a similar shape and placement in both frames. Align its perspective and keep light and texture compatible around the rim.",
    check: "Check that one opening carries the move; reject nested tunnels, changing scale or a second arrival.",
    text: "The camera connects the supplied first and last frames through one opening anchored to the shape described in the notes. Follow the specified route as the opening reveals the destination within it, using the existing scene's color and texture for a clean rim. Complete the passage in one continuous move and ease into the final composition with the endpoint subjects recognizable." },
  { id: "material", name: "Material dissolve", group: "experimental", directions: ["auto", "left", "right"],
    fit: "Choose matching silhouettes and one visible texture such as fabric, smoke, sand or liquid. Give that material a clear travel direction and match its color to the destination.",
    check: "Check that a single material carries the change; reject damaged anatomy or residue covering the incoming face.",
    text: "Use the single material described in the notes to connect the supplied first and last frames. Let that existing texture carry the silhouette change in one flowing movement through the middle while the shared anchor stays aligned. Reconstruct the destination as the material settles, revealing clear anatomy and the final scene's texture. Ease into the incoming subject's motion as the transformation finishes." },
  { id: "scan", name: "Point-cloud scan", group: "experimental", directions: ["auto", "left", "right"],
    fit: "Choose clear architecture or strong silhouettes with a shared perspective anchor. A consistent scan direction and similar scene colors help this deliberate digital accent resolve cleanly.",
    check: "Check the spatial anchor, readable forms and complete texture recovery; reject floating debris or a neon overlay at either join.",
    text: "Connect the supplied first and last frames with one spatial scan moving in the direction described in the notes. Its leading edge briefly turns surfaces into sparse points colored from the footage; its trailing edge rebuilds the destination. Keep the dominant silhouette and perspective anchor readable through the middle. Finish the sweep with the final frame's photographic texture fully restored and natural incoming motion." },
];

const ENERGY = {
  restrained: "Keep the treatment restrained: one brief central accent, minimal distortion, natural exposure, and a gentle arrival that continues the incoming motion.",
  balanced: "Use a clear central accent with a smooth build and controlled recovery. Keep the transition readable and reserve the strongest change for its middle.",
  bold: "Make the central transition decisive, with a faster build and stronger treatment. Keep it one coherent move, with controlled entry and a smooth recovery into the incoming motion.",
};
const TRAVEL = {
  auto: "", left: "Camera travel is leftward; maintain that screen direction through the move.",
  right: "Camera travel is rightward; maintain that screen direction through the move.",
  forward: "Move the camera forward through the described open route, maintaining that direction into the arrival.",
  backward: "Pull the camera backward through the described open route, maintaining that direction into the arrival.",
};

export function buildBridgePrompt(id: string, motionNotes = "", options?: BridgeDirectionOptions): string {
  const template = BRIDGE_PROMPTS.find((item) => item.id === id);
  if (!template) throw new Error("Choose a bridge prompt starter.");
  const notes = motionNotes.trim();
  const anchor = options?.anchor?.trim() ?? "";
  if (notes.length > 2000) throw new Error("Keep motion notes within 2000 characters.");
  if (anchor.length > 240) throw new Error("Keep the visual anchor within 240 characters.");
  if (options?.energy !== undefined && !Object.hasOwn(ENERGY, options.energy)) throw new Error("Choose a transition energy.");
  if (options?.direction !== undefined && !template.directions.includes(options.direction)) throw new Error("Choose a direction supported by this technique.");
  return [template.text,
    options?.energy ? ENERGY[options.energy] : "",
    options?.direction ? TRAVEL[options.direction] : "",
    anchor ? "Visual anchor to preserve across the transition: " + anchor : "",
    notes ? "Motion notes from reviewing the source clips:\n" + notes : "",
  ].filter(Boolean).join("\n\n");
}
