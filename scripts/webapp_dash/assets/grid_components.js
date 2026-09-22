/* Community AG Grid components. Images are loaded only from the local app. */
window.dashAgGridFunctions = window.dashAgGridFunctions || {};
window.dashAgGridFunctions.scopeRowId = function (row) {
    let parts;
    if (Object.hasOwn(row, 'structure_count') && row.formula) parts = ['formula', row.formula];
    else if (row.folder && row.name) parts = ['source', row.folder, row.name];
    else {
        const key = ['event_id', 'id', 'reaction_key', 'reaction_smiles', 'species_file',
            'smiles', 'target_smiles', 'formula', 'name'].find(k => row[k] != null && row[k] !== '');
        parts = key ? [key, row[key]] : ['row', Object.fromEntries(Object.entries(row).sort())];
    }
    return JSON.stringify(parts);
};
window.dashAgGridComponentFunctions = window.dashAgGridComponentFunctions || {};
window.dashAgGridComponentFunctions.ScopeStructureTooltip = function (props) {
    const row = props.data || {};
    const preview = ((props.context || {}).previews || {})[window.dashAgGridFunctions.scopeRowId(row)] || {};
    const markdown = (preview[props.colDef.field] || {}).value || '';
    const match = markdown.match(/!\[[^\]]*\]\((\/api\/(?:structure|reaction)\.svg\?[^\s]*)\)/);
    return React.createElement('div', {className: 'rs-structure-tooltip'},
        React.createElement('strong', null, row.reaction_formulas || row.formula || props.value),
        match ? React.createElement('img', {src: match[1], alt: '结构预览'}) : null,
        React.createElement('code', null, row.reaction_smiles || row.smiles || props.value));
};
