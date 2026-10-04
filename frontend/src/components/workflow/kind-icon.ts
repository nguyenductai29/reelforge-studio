import {
  AudioLines,
  Captions,
  Clapperboard,
  Eye,
  FileText,
  Film,
  Image as ImageIcon,
  Inbox,
  Scissors,
  Send,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import type { NodeKind } from "@/lib/workflow";

export const kindIcon: Record<NodeKind, LucideIcon> = {
  input: Inbox,
  ai: Sparkles,
  script: FileText,
  image: ImageIcon,
  video: Film,
  voice: AudioLines,
  subtitle: Captions,
  edit: Scissors,
  render: Clapperboard,
  review: Eye,
  publish: Send,
};
