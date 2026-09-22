"""Interactive audit of exact segments and their authored RNG occurrences."""
from __future__ import annotations

import json
from uuid import uuid4

import dash_bootstrap_components as dbc
import dash_cytoscape as cyto
from dash import Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from reacnet_scope import services as svc
from .candidate_workbench import context_key, bond_graph


def layout():
    return html.Details([html.Summary('可选：追踪参与分子的此前或后续变化'),
        dbc.Card(dbc.CardBody([
        html.H5('分子变化追踪'),
        html.P('从当前具体反应实例中选择一个参与分子，按记录展开它此前或后续的变化。'
               '这项追踪不判断候选路线是否成立。'),
        html.Div(id='lx-capability', role='status'),
        dcc.Dropdown(id='lx-instance', placeholder='先在事件工作区选择一个具体事件', clearable=False),
        dbc.Label('路径锚点（更改后重新打开 instance）', className='mt-2'),
        dcc.Dropdown(id='lx-anchor-mode', value='all_atoms', clearable=False, options=[
            {'label': '起点全部原子', 'value': 'all_atoms'},
            {'label': '碳原子', 'value': 'carbon'},
            {'label': '全部非氢原子', 'value': 'heavy_atoms'},
            {'label': '指定 Atom IDs', 'value': 'atom_ids'}]),
        dcc.Input(id='lx-anchor-ids', type='text', placeholder='指定 Atom IDs，例如 301,302,303', style={'width':'100%'}),
        html.Details([html.Summary('元素映射（可选）'),
            html.P('默认读取已索引原始轨迹的起点精确帧。也可提供 Atom ID → 元素 JSON；不会从分子式推测。'),
            dcc.Textarea(id='lx-atom-elements', placeholder='例如 {"301":"C","310":"Cl"}', style={'width':'100%'})]),
        dbc.Button('开始追踪所选分子', id='lx-start', className='mt-2 me-2'),
        dbc.Button('回到起始分子', id='lx-home', outline=True, className='mt-2 me-2'),
        dbc.Button('导出当前变化图与历史', id='lx-export', outline=True, className='mt-2'),
        html.Div(id='lx-message', role='status', className='my-2'),
        dcc.RadioItems(id='lx-view', options=[{'label':'变化关系图', 'value':'lineage'},
                                            {'label':'单条变化历史', 'value':'path'}], value='lineage', inline=True),
        html.Div([
            dcc.Dropdown(id='lx-target', placeholder='从已展开关系图选择目标分子段', clearable=False),
            dbc.Button('提取单条变化历史', id='lx-paths', className='my-2'),
            dcc.Dropdown(id='lx-path-choice', placeholder='选择一条有连续证据的变化历史', clearable=False),
            html.Div(id='lx-path-summary'),
        ], id='lx-path-controls', style={'display':'none'}),
        cyto.Cytoscape(id='lx-graph', elements=[], layout={'name':'breadthfirst', 'directed':True, 'padding':30},
            style={'width':'100%', 'height':'460px'}, stylesheet=[
                {'selector':'node', 'style':{'label':'data(label)', 'font-size':11, 'text-wrap':'wrap',
                                            'text-max-width':180, 'background-color':'#3877bb', 'color':'#263346'}},
                {'selector':'node[kind = "event"]', 'style':{'shape':'diamond', 'background-color':'#d58b24'}},
                {'selector':'node[root = 1]', 'style':{'border-width':3, 'border-color':'#123757'}},
                {'selector':'edge', 'style':{'curve-style':'bezier', 'target-arrow-shape':'triangle', 'width':1.5}},
                {'selector':'.context', 'style':{'line-style':'dashed', 'target-arrow-shape':'none'}},
                {'selector':'.side-participant', 'style':{'opacity':0.55}},
                {'selector':'edge.carrier', 'style':{'width':3, 'line-color':'#176bb0', 'target-arrow-color':'#176bb0'}},
            ]),
        html.Div([
            dbc.Button('上一事件', id='lx-prev', outline=True, className='me-2'),
            dbc.Button('下一事件', id='lx-next', outline=True, className='me-2'),
            dbc.Button('展开全部分支', id='lx-all', outline=True, className='me-2'),
            dbc.Button('沿该分支继续', id='lx-continue', className='me-2'),
            dbc.Button('查看原始 reaction occurrence', id='lx-event', outline=True),
        ], className='my-2'),
        html.Div(id='lx-detail'),
        html.Div([
            dbc.Label('分析帧（所选 Segment 的范围内）', html_for='lx-frame'),
            dcc.Input(id='lx-frame', type='number', step=1, min=0),
            dbc.Button('查看原始 frame', id='lx-frame-show', outline=True, className='ms-2'),
        ], className='my-2'),
        html.Div(id='lx-audit'),
        dcc.Download(id='lx-download'),
        *[dcc.Store(id=f'lx-{key}') for key in ['request','raw','state','focus']],
    ]), className='rs-card')], className='rs-lineage-explorer-details', id='lx-card')


def graph_elements(report, path=None):
    if not report:
        return []
    selected_segments = set(path['segments']) if path else set(report['segments'])
    selected_events = set(path['events']) if path else set(report['occurrences'])
    carrier_segments = set(selected_segments)
    carrier_ports = set()
    if path:
        for i, eid in enumerate(path['events']):
            event = report['occurrences'][eid]
            selected_segments.update(event['input_segments'] + event['output_segments'])
            carrier_ports.update([(eid, 'input', path['segments'][i]), (eid, 'output', path['segments'][i+1])])
    result = []
    for sid in sorted(selected_segments):
        s = report['segments'][sid]
        result.append({'data':{'id':sid, 'kind':'segment', 'root':int(sid==report['root']['segment_id']),
            'label':f"{s['species']}\nframes {s['start_frame']}–{s['end_frame']}"},
            'classes':'side-participant' if sid not in carrier_segments else ''})
    for eid in sorted(selected_events):
        e = report['occurrences'][eid]
        result.append({'data':{'id':eid, 'kind':'event', 'label':f"Transition {e['transition_index']}"}})
        unchanged = set(e['input_segments']) & set(e['output_segments'])
        for side, segments in [('input',e['input_segments']),('output',e['output_segments'])]:
            for i, sid in enumerate(segments):
                if sid not in selected_segments or side == 'output' and sid in unchanged:
                    continue
                source, target = (sid,eid) if side=='input' else (eid,sid)
                result.append({'data':{'id':f'{eid}:{side}:{i}', 'source':source, 'target':target},
                               'classes':'context' if sid in unchanged else 'carrier' if (eid, side, sid) in carrier_ports else ''})
    return result


def detail_view(report, focus):
    if not report or not focus:
        return '', None, 0, None
    sid = focus.get('id')
    if sid in report['segments']:
        s = report['segments'][sid]
        fields = [('segment_id', sid), ('frame range',f"{s['start_frame']}–{s['end_frame']}"),
                  ('source timestep',f"{s['start_timestep']}–{s['end_timestep']}"), ('Species',s['species']),
                  ('Atom IDs',s['atom_ids']), ('锚点规则',report.get('anchor_policy')),
                  ('Retained / lost / gained（相对所选起点锚点；非路径连续性）',
                  [s['retained_atoms'],s['lost_atoms'],s['gained_atoms']]),
                  ('Connected components',s['connected_components']),
                  ('Retained components',s['retained_components']),
                  ('来源 occurrence',s['previous']), ('下一次 state-changing occurrence',s['next'])]
        content = [html.H6('Continuity Segment'),
                   html.Dl([html.Div([html.Dt(k),html.Dd(str(v))]) for k,v in fields]),
                   bond_graph([{'atom_ids':s['atom_ids']}],s['bonds'])]
        frame = report['root']['analyzed_frame'] if sid == report['root']['segment_id'] else s['start_frame']
        return html.Div(content), frame, s['start_frame'], s['end_frame']
    if sid in report['occurrences']:
        e = report['occurrences'][sid]
        return html.Div([html.H6('Reaction Occurrence'),html.P(e['reaction_type']),
                         html.P(f"Transition {e['transition_index']}；split={e['split']}，merge={e['merge']}"),
                         html.P('Input segments: '+', '.join(e['input_segments'])),
                         html.P('Output segments: '+', '.join(e['output_segments'])),
                         html.P('Broken bonds: '+', '.join(e['broken_bonds'])),
                         html.P('Formed bonds: '+', '.join(e['formed_bonds'])),
                         html.Details([html.Summary('Atom provenance（每个来源 → 去向）'),
                                       html.Pre(json.dumps(e['provenance'],ensure_ascii=False,indent=2))])]), None, 0, None
    return '', None, 0, None


def path_details(path):
    if not path:
        return ''
    rows = []
    for step in path['steps']:
        chlorine = step['chlorine']
        rows.append(html.Tr([html.Td(str(step['transition_index'])),
            html.Td(step['reaction_type']), html.Td(str(step['continuously_retained_anchor_ids'])),
            html.Td(str(step['departed_atom_ids'])), html.Td(str(step['returned_root_atom_ids'])),
            html.Td(str(step['external_atom_ids_added'])),
            html.Td(f"离开 {chlorine['departed_ids']} / 原 Cl 返回 {chlorine['original_ids_returned']} / 外来 Cl {chlorine['external_ids_added']}；映射 {chlorine['mapping_status']}")]))
    return html.Div([
        html.P(f"锚点 {path['anchor_atom_ids']}；全程保留 {path['continuously_retained_anchor_ids']}。原子重新出现不恢复已中断的路径连续性。"),
        html.P('蓝色粗边为所选路径；浅色节点保留同一事件的其他参与者。外来指不属于起始实例，未必首次加入。'),
        dbc.Table([html.Thead(html.Tr([html.Th(s) for s in ['Transition','完整反应','全程保留锚点','离开 IDs','起点原子返回','外来加入 IDs','Cl 来源']])),
                   html.Tbody(rows)], bordered=True, responsive=True, size='sm'),
        html.P(f"检测到 {len(path['return_episodes'])} 段状态返回；不自动判定为噪声或净生成。"),
        html.Details([html.Summary('原子来源、支路追溯及返回证据'),
                      html.Pre(json.dumps({'steps':path['steps'], 'return_episodes':path['return_episodes']},ensure_ascii=False,indent=2))])])


def accept_payload(raw, request, store):
    return bool(raw and request and raw.get('request_id')==request.get('request_id')
                and raw.get('context')==request.get('context')==context_key(store))


def register_callbacks(app):
    @app.callback(Output('lx-instance','options'), Output('lx-instance','value'),
                  Output('lx-capability','children'), Input('event-selected-store','data'), Input('app-store','data'))
    def prepare(selected, store):
        options = []
        row = (selected or {}).get('row') or {}
        for side, label in [('reactant','反应物'),('product','产物')]:
            for i, p in enumerate(row.get(f'{side}_participants') or []):
                options.append({'label':f"{label} {i+1} · {p['species']} · atoms {p['atom_ids']}", 'value':f'{side}:{i}'})
        status = svc.lineage_explorer_status((store or {}).get('artifacts') or {})
        return options, options[0]['value'] if options else None, status['message']

    @app.callback(Output('lx-request','data'),
                  *[Input('lx-'+key,'n_clicks') for key in ['start','prev','next','all','continue','paths','frame-show','event']],
                  Input('app-store','data'), State('event-selected-store','data'), State('lx-instance','value'),
                  State('lx-state','data'), State('lx-focus','data'), State('lx-target','value'), State('lx-frame','value'),
                  State('lx-anchor-mode','value'), State('lx-anchor-ids','value'), State('lx-atom-elements','value'))
    def request(*args):
        store, selected, instance, state, focus, target, frame, anchor_mode, anchor_ids, atom_elements = args[8:]
        if ctx.triggered_id == 'app-store' or not ctx.triggered_id:
            return None
        action = ctx.triggered_id.removeprefix('lx-')
        report = (state or {}).get('report')
        if action != 'start' and (not report or state.get('context') != context_key(store)):
            raise PreventUpdate
        return dict(request_id=uuid4().hex, context=context_key(store), action=action,
                    artifacts=(store or {}).get('artifacts') or {}, selected=selected, instance=instance,
                    report=report, focus=focus or {}, target=target, frame=frame,
                    anchor_mode=anchor_mode, anchor_ids=anchor_ids, atom_elements=atom_elements)

    @app.callback(Output('lx-raw','data'), Input('lx-request','data'), background=True,
                  running=[(Output('lx-start','disabled'), True, False), (Output('lx-all','disabled'),True,False)],
                  prevent_initial_call=True)
    def run(request):
        if not request:
            return None
        payload = {k:request[k] for k in ('request_id','context','action')}
        try:
            artifacts, report, action = request['artifacts'], request['report'], request['action']
            focus = request['focus'].get('id')
            if action == 'start':
                if not request['instance']:
                    raise svc.ServiceError('请先选择事件中的具体 molecule instance。')
                side, i = request['instance'].split(':')
                mode = request.get('anchor_mode', 'all_atoms')
                mapping = json.loads(request['atom_elements']) if request.get('atom_elements') else None
                if mapping is not None and not isinstance(mapping, dict):
                    raise svc.ServiceError('元素映射必须为 JSON 对象。')
                report = svc.start_lineage_explorer(artifacts, event_id=request['selected']['row']['event_id'],
                    side=side, participant_index=int(i), anchor_mode='elements' if mode=='carbon' else mode,
                    anchor_elements=['C'] if mode=='carbon' else [],
                    anchor_atom_ids=[int(a) for a in (request.get('anchor_ids') or '').replace(',', ' ').split()] if mode=='atom_ids' else [],
                    atom_elements=mapping)
                payload['report'] = report
                payload['focus'] = {'id':report['root']['segment_id'], 'kind':'segment'}
            elif action in {'prev','next','all','continue'}:
                direction = 'backward' if action=='prev' else 'forward'
                if action!='all' and focus not in report['segments']:
                    raise svc.ServiceError('请点击要追踪的 Segment。')
                expanded = svc.expand_lineage_explorer(artifacts, report, segment_id=focus or '',
                                                       direction=direction, all_branches=action=='all')
                payload['report'] = expanded
                event = (expanded['segments'].get(focus, {}).get('previous' if direction=='backward' else 'next') or {}).get('event_id')
                if event and event in expanded['occurrences']:
                    payload['focus'] = {'id':event,'kind':'event'}
            elif action=='paths':
                payload['paths'] = svc.observed_lineage_paths(artifacts, report, request['target'])
            elif action=='frame-show':
                if focus not in report['segments']:
                    raise svc.ServiceError('请点击 Segment 后选择原始 frame。')
                payload['frame'] = svc.lineage_frame_data(artifacts,report,focus,request['frame'])
            elif action=='event':
                if focus not in report['occurrences']:
                    raise svc.ServiceError('请先点击 Reaction Occurrence。')
                payload['occurrence'] = svc.lineage_occurrence_record(artifacts,report,focus)
        except (svc.ServiceError, ValueError, TypeError, KeyError) as exc:
            payload['error'] = str(exc)
        return payload

    @app.callback(Output('lx-state','data'), Input('lx-raw','data'), Input('lx-request','data'),
                  Input('app-store','data'), State('lx-state','data'))
    def commit(raw, request, store, current):
        if not request:
            return None
        if not accept_payload(raw, request, store):
            return no_update if current and current.get('context')==context_key(store) else None
        result = dict(current or {}) if raw['action']!='start' else {}
        if raw.get('report'):
            for key in ['paths','frame','occurrence']:
                result.pop(key,None)
        result.pop('error',None)
        result.pop('focus',None)
        result.update(raw)
        return result

    @app.callback(Output('lx-focus','data'), Input('lx-graph','tapNodeData'), Input('lx-state','data'),
                  Input('lx-home','n_clicks'), State('lx-focus','data'))
    def focus(tap, state, _home, current):
        report = (state or {}).get('report')
        if not report:
            return None
        if ctx.triggered_id=='lx-home':
            return {'id':report['root']['segment_id'],'kind':'segment'}
        if ctx.triggered_id=='lx-graph' and tap:
            return {'id':tap['id'],'kind':tap['kind']}
        return state.get('focus') or current or {'id':report['root']['segment_id'],'kind':'segment'}

    @app.callback(Output('lx-graph','elements'), Output('lx-message','children'),
                  Output('lx-target','options'), Output('lx-path-choice','options'), Output('lx-path-choice','value'),
                  Output('lx-path-controls','style'), Output('lx-path-summary','children'),
                  Input('lx-state','data'), Input('lx-view','value'), Input('lx-path-choice','value'), Input('app-store','data'))
    def render(state, view, choice, store):
        if not state or state.get('context')!=context_key(store) or not state.get('report'):
            return [], (state or {}).get('error',''), [], [], None, {'display':'none'}, ''
        report = state['report']
        paths = (state.get('paths') or {}).get('paths',[])
        choice = choice if isinstance(choice,int) and 0 <= choice < len(paths) else 0 if paths else None
        selected = paths[choice] if view=='path' and choice is not None else None
        elements = graph_elements(report, selected) if view!='path' or selected else []
        status = report.get('last_action',{}).get('status')
        labels = {'ready':'已定位起始 instance。','complete':'已展开。','available':'已展开。',
                  'stop':'该分支已按规则停止。','observation_boundary':'已到观测边界。',
                  'query_budget':'本次展开达到预算，可继续展开。','graph_budget':'当前图已达交互预算。'}
        message = state.get('error') or f"{len(report['segments'])} segments / {len(report['occurrences'])} occurrences。" + labels.get(status,'')
        options = [{'label':f"frames {s['start_frame']}–{s['end_frame']} · {s['species']} · {sid[-8:]}",'value':sid}
                   for sid,s in report['segments'].items() if sid!=report['root']['segment_id']]
        summary = (f'在已展开的 lineage 中提取 {len(paths)} 条路径；'
                   + ('提取达到预算。' if state.get('paths',{}).get('truncated') else '未展开分支不在本次提取范围内。'))
        return elements,message,options,[{'label':f"路径 {i+1} · {len(p['events'])} events · 保留 atoms {p['retained_atoms']}",
                                         'value':i} for i,p in enumerate(paths)],choice,{'display':'block' if view=='path' else 'none'},html.Div([summary, path_details(selected)])

    @app.callback(Output('lx-detail','children'),Output('lx-frame','value'),Output('lx-frame','min'),Output('lx-frame','max'),
                  Input('lx-state','data'), Input('lx-focus','data'), Input('app-store','data'))
    def detail(state, focus, store):
        if not state or state.get('context')!=context_key(store):
            return '',None,0,None
        return detail_view(state.get('report'),focus)

    @app.callback(Output('lx-audit','children'), Input('lx-state','data'), Input('app-store','data'))
    def audit(state, store):
        if not state or state.get('context')!=context_key(store):
            return ''
        if state.get('action')=='frame-show' and state.get('frame'):
            import plotly.graph_objects as go
            frame = state['frame']
            atoms = frame['atoms']
            fig = go.Figure(go.Scatter3d(x=[a['x'] for a in atoms],y=[a['y'] for a in atoms],z=[a['z'] for a in atoms],
                text=[f"{a['label']} · ID {a['id']}" for a in atoms],mode='markers+text'))
            fig.update_layout(scene={'aspectmode':'data'},height=350)
            return html.Div([html.P(str(frame['reference'])),dcc.Graph(figure=fig),
                             html.Details([html.Summary('原始 frame 文本'),html.Pre(frame['raw_text'])])])
        if state.get('action')=='event' and state.get('occurrence'):
            return html.Details([html.Summary('原始 Reaction Occurrence 记录'),
                                 html.Pre(json.dumps(state['occurrence'],ensure_ascii=False,indent=2))],open=True)
        return ''

    @app.callback(Output('lx-download','data'), Output('lx-message','children', allow_duplicate=True),
                  Input('lx-export','n_clicks'), State('lx-state','data'), State('app-store','data'),
                  prevent_initial_call=True)
    def export(click, state, store):
        if not click or not state or state.get('context')!=context_key(store):
            raise PreventUpdate
        try:
            result = svc.export_lineage_explorer((store or {}).get('artifacts') or {}, state['report'],
                target_segment=(state.get('paths') or {}).get('target_segment'))
        except (svc.ServiceError, ValueError, KeyError) as exc:
            return no_update, str(exc)
        return dcc.send_string(json.dumps(result, ensure_ascii=False,indent=2),'molecular-lineage-explorer.json'), no_update
