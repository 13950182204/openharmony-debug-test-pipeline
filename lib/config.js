import z from 'schemastery';
const ladderTierSchema = z.object({
    name: z.string(),
    provider: z.string(),
    model: z.string(),
    maxTokens: z.number(),
});
export const Config = z.object({
    stateFile: z.string().default('~/.dsh/pipeline-state.json'),
    wslPackageRoot: z.string(),
    subagentProvider: z.string().default('fork'),
    stateFileModel: z.string(),
    reviewLadder: z.array(ladderTierSchema),
});
