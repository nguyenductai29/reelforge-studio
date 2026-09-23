"use client";
import { useMemo, useState } from "react";
import { ReactFlow, Background, Controls, Handle, Position, addEdge, useEdgesState, useNodesState, type Connection, type Edge, type Node, type NodeProps } from "@xyflow/react";

export type Graph = { nodes: { id: string; type: string; x: number; y: number }[]; edges: { source: string; target: string }[] };
export type Workflow = { id: string; name: string; graph: Graph };
const catalog: Record<string, { label: string; group: string; icon: string; detail: string }> = {
  idea:{label:"Ý tưởng",group:"Nội dung",icon:"✦",detail:"Điểm bắt đầu của video."},
  script:{label:"Kịch bản AI",group:"Nội dung",icon:"≡",detail:"Bản thảo lời dẫn."},
  scenes:{label:"Phân cảnh",group:"Nội dung",icon:"▦",detail:"Chia câu chuyện thành cảnh."},
  image:{label:"Tạo ảnh",group:"Media",icon:"▧",detail:"Minh họa từng cảnh."},
  video:{label:"Tạo clip",group:"Media",icon:"▣",detail:"Clip từ prompt hoặc ảnh."},
  assets:{label:"Kho media",group:"Media",icon:"▤",detail:"Chọn tài nguyên đã có."},
  voice:{label:"Giọng đọc",group:"Âm thanh",icon:"◖",detail:"Tạo lời dẫn từ kịch bản."},
  music:{label:"Nhạc & SFX",group:"Âm thanh",icon:"♫",detail:"Nhạc và hiệu ứng."},
  subtitle:{label:"Phụ đề",group:"Hoàn thiện",icon:"▥",detail:"Tạo và căn phụ đề."},
  render:{label:"Render",group:"Hoàn thiện",icon:"◈",detail:"Ghép video hoàn chỉnh."},
  review:{label:"Duyệt",group:"Xuất bản",icon:"✓",detail:"Kiểm tra trước khi đăng."},
  publish:{label:"Đăng tải",group:"Xuất bản",icon:"↗",detail:"Gửi tới các kênh."},
};
type StudioNode = Node<{ kind: string }, "studio">;
function NodeView({ data, selected }: NodeProps<StudioNode>) {
  const item=catalog[data.kind]??catalog.idea;
  return <div className={`flow-node ${selected?"active":""}`}><Handle type="target" position={Position.Left}/><div className="flow-node-icon">{item.icon}</div><div><small>{item.group}</small><strong>{item.label}</strong></div><Handle type="source" position={Position.Right}/></div>;
}
const nodeTypes={studio:NodeView};
const initialNodes=(graph:Graph):StudioNode[]=>graph.nodes.map(n=>({id:n.id,type:"studio",position:{x:n.x,y:n.y},data:{kind:n.type}}));
const initialEdges=(graph:Graph):Edge[]=>graph.edges.map((e,i)=>({id:`edge-${i}-${e.source}-${e.target}`,source:e.source,target:e.target,type:"smoothstep"}));
export default function FlowEditor({workflow,onSave}:{workflow:Workflow;onSave:(graph:Graph)=>Promise<boolean>}){
  const [nodes,setNodes,onNodesChange]=useNodesState<StudioNode>(initialNodes(workflow.graph));
  const [edges,setEdges,onEdgesChange]=useEdgesState<Edge>(initialEdges(workflow.graph));
  const [selected,setSelected]=useState<string|null>(null);
  const [dirty,setDirty]=useState(false);
  const [saving,setSaving]=useState(false);
  const groups=useMemo(()=>[...new Set(Object.values(catalog).map(c=>c.group))],[]);
  const selectedNode=nodes.find(n=>n.id===selected);
  const addNode=(kind:string)=>{const id=`n${crypto.randomUUID().replaceAll("-","").slice(0,16)}`;setNodes(current=>[...current,{id,type:"studio",position:{x:150+current.length*75,y:120+(current.length%4)*90},data:{kind}}]);setSelected(id);setDirty(true)};
  const remove=()=>{if(!selected)return;setNodes(current=>current.filter(n=>n.id!==selected));setEdges(current=>current.filter(e=>e.source!==selected&&e.target!==selected));setSelected(null);setDirty(true)};
  const connect=(connection:Connection)=>{if(!connection.source||!connection.target||connection.source===connection.target)return;const seen=new Set<string>();const reaches=(id:string):boolean=>{if(id===connection.source)return true;if(seen.has(id))return false;seen.add(id);return edges.filter(e=>e.source===id).some(e=>reaches(e.target))};if(reaches(connection.target)||edges.some(e=>e.source===connection.source&&e.target===connection.target))return;setEdges(current=>addEdge({...connection,type:"smoothstep"},current));setDirty(true)};
  const save=async()=>{setSaving(true);try{const graph={nodes:nodes.map(n=>({id:n.id,type:n.data.kind,x:n.position.x,y:n.position.y})),edges:edges.map(e=>({source:e.source,target:e.target}))};if(await onSave(graph))setDirty(false)}finally{setSaving(false)}};
  return <div className="editor-shell"><div className="editor-top"><div><p className="eyebrow">VISUAL WORKFLOW BUILDER</p><h2>{workflow.name}</h2><span className="subtle">Kéo node để sắp xếp · kéo điểm nối để tạo luồng</span></div><div className="editor-actions"><span className="editor-state">{dirty?"● Chưa lưu":"✓ Đã lưu"}</span><button className="primary-action" onClick={save} disabled={!dirty||saving}>{saving?"Đang lưu…":"Lưu sơ đồ"}</button></div></div>
  <div className="editor-body"><aside className="node-palette"><h3>THƯ VIỆN NODE</h3><p>Nhấn để thêm vào sơ đồ</p>{groups.map(group=><section key={group}><h4>{group}</h4>{Object.entries(catalog).filter(([,item])=>item.group===group).map(([key,item])=><button type="button" key={key} onClick={()=>addNode(key)}><span>{item.icon}</span>{item.label}<b>＋</b></button>)}</section>)}</aside>
  <div className="flow-stage" aria-label="Sơ đồ workflow"><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={changes=>{onNodesChange(changes);if(changes.some(c=>c.type==="position"||c.type==="remove"||c.type==="add"))setDirty(true)}} onEdgesChange={changes=>{onEdgesChange(changes);if(changes.some(c=>c.type==="remove"||c.type==="add"))setDirty(true)}} onConnect={connect} onNodeClick={(_,node)=>setSelected(node.id)} onPaneClick={()=>setSelected(null)} fitView fitViewOptions={{padding:.15}} minZoom={.3} maxZoom={1.7} proOptions={{hideAttribution:true}}><Background color="#354055" gap={22} size={1}/><Controls showInteractive={false}/></ReactFlow><span className="flow-stage-label">SƠ ĐỒ MẪU · CHƯA THỰC THI</span></div>
  <aside className="node-inspector"><h3>CHI TIẾT NODE</h3>{selectedNode?<><div className="inspector-icon">{catalog[selectedNode.data.kind]?.icon}</div><h2>{catalog[selectedNode.data.kind]?.label}</h2><p>{catalog[selectedNode.data.kind]?.detail}</p><div className="inspector-line"><span>Nhóm</span><b>{catalog[selectedNode.data.kind]?.group}</b></div><div className="inspector-line"><span>Trạng thái</span><b>Chưa cấu hình</b></div><button className="ghost-danger" onClick={remove}>Xóa node</button></>:<p>Chọn một node để xem chi tiết. Mỗi node sẽ được cấu hình khi engine hoàn thiện.</p>}</aside></div>
  <div className="editor-footer"><span>{nodes.length} node · {edges.length} kết nối</span><span>Sơ đồ lưu trong PostgreSQL theo workspace; chưa chạy AI, render hoặc đăng tải.</span></div></div>;
}
