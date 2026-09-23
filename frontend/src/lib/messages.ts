export const messages = {
  loading: "Đang tải dữ liệu…",
  saving: "Đang lưu…",
  saved: "Đã lưu thay đổi.",
  created: "Đã tạo tài khoản và studio.",
  failed: "Không thể hoàn thành yêu cầu. Vui lòng thử lại.",
  confirm: "Xác nhận",
  cancel: "Hủy",
} as const;

const serverMessages: Record<string, string> = {
  "Please sign in": "Vui lòng đăng nhập lại.",
  "System admin required": "Bạn cần quyền quản trị hệ thống.",
  "Email already exists": "Email này đã được sử dụng.",
  "Registration is closed": "Studio hiện tạm ngừng đăng ký tài khoản mới.",
  "Create the first studio as administrator": "Cần tạo tài khoản quản trị đầu tiên trước.",
  "Trial plan unavailable": "Gói Trial hiện không khả dụng.",
  "Plan unavailable": "Gói này hiện không khả dụng.",
  "Workspace subscription is inactive": "Gói dịch vụ của studio đã ngừng hoạt động.",
  "Cannot disable your own account": "Bạn không thể vô hiệu hóa chính mình.",
  "Cannot disable the last system admin": "Không thể khóa quản trị viên cuối cùng.",
  "Move active subscriptions before disabling this plan": "Cần chuyển các studio đang dùng gói này trước khi tắt gói.",
};
export function errorMessage(error: unknown): string {
  const detail = error instanceof Error ? error.message : String(error);
  return serverMessages[detail] ?? detail ?? messages.failed;
}
