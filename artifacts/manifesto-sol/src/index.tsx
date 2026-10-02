import React from "react";
import {registerRoot, Composition} from "remotion";
import {Film} from "./Film";
const Root=()=> <Composition id="Continuity" component={Film} durationInFrames={2520} fps={30} width={1920} height={1080}/>;
registerRoot(Root);
