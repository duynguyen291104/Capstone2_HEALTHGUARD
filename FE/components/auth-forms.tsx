"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Eye, EyeOff, LoaderCircle, Mail } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useAuth } from "@/components/auth-provider";
import { Button, ErrorNotice, Field } from "@/components/ui";
import { authApi, getErrorMessage } from "@/lib/api";
import type { InvitationInspection } from "@/lib/types";
import { safeNextPath } from "@/lib/workflow";

const loginSchema = z.object({
  email: z.string().trim().email("Email chưa đúng định dạng."),
  password: z.string().min(1, "Hãy nhập mật khẩu.").max(128, "Mật khẩu tối đa 128 ký tự."),
});

type LoginValues = z.infer<typeof loginSchema>;

export function LoginForm({ nextPath = "" }: { nextPath?: string }) {
  const router = useRouter();
  const { setUser } = useAuth();
  const [showPassword, setShowPassword] = useState(false);
  const [serverError, setServerError] = useState("");
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
  });

  async function onSubmit(values: LoginValues) {
    setServerError("");
    try {
      const user = await authApi.login(values);
      setUser(user);
      const safeNext = safeNextPath(nextPath);
      router.replace(safeNext || (user.current_group ? "/hom-nay" : "/tao-nhom"));
    } catch (error) {
      setServerError(getErrorMessage(error));
    }
  }

  return (
    <div className="auth-card">
      <div className="auth-heading">
        <p className="eyebrow">Chào mừng trở lại</p>
        <h1>Đăng nhập</h1>
        <p>Tiếp tục quản lý lịch chăm sóc của gia đình.</p>
      </div>
      {serverError ? <ErrorNotice message={serverError} /> : null}
      <form className="form-stack" onSubmit={handleSubmit(onSubmit)} noValidate>
        <Field label="Email" type="email" autoComplete="email" placeholder="ten@email.com" error={errors.email?.message} {...register("email")} />
        <div className="password-field">
          <Field label="Mật khẩu" type={showPassword ? "text" : "password"} autoComplete="current-password" error={errors.password?.message} {...register("password")} />
          <button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"}>
            {showPassword ? <EyeOff size={19} /> : <Eye size={19} />}
          </button>
        </div>
        <Button type="submit" loading={isSubmitting} className="button--full">Đăng nhập</Button>
      </form>
      <p className="auth-switch">Chưa có tài khoản? <Link href="/dang-ky">Tạo tài khoản</Link></p>
      <div className="auth-help"><Mail size={17} /><span>Người chăm sóc cần mở đúng liên kết mời do chủ gia đình gửi.</span></div>
    </div>
  );
}

const registrationFields = {
  full_name: z.string().trim().min(1, "Hãy nhập họ tên.").max(150, "Họ tên tối đa 150 ký tự."),
  email: z.string().trim().email("Email chưa đúng định dạng."),
  password: z.string().min(10, "Mật khẩu cần ít nhất 10 ký tự.").max(128, "Mật khẩu tối đa 128 ký tự."),
  confirm_password: z.string(),
};

const matchingPasswords = {
  message: "Hai mật khẩu chưa trùng nhau.",
  path: ["confirm_password"],
};

const accountSchema = z.object(registrationFields).refine(
  (data) => data.password === data.confirm_password,
  matchingPasswords,
);

type AccountValues = z.infer<typeof accountSchema>;

export function AccountRegisterForm() {
  const router = useRouter();
  const { setUser } = useAuth();
  const [serverError, setServerError] = useState("");
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<AccountValues>({ resolver: zodResolver(accountSchema) });

  async function onSubmit(values: AccountValues) {
    setServerError("");
    try {
      const user = await authApi.register({
        full_name: values.full_name,
        email: values.email,
        password: values.password,
      });
      setUser(user);
      router.replace("/tao-nhom");
    } catch (error) {
      setServerError(getErrorMessage(error));
    }
  }

  return (
    <div className="auth-card auth-card--wide">
      <div className="auth-heading">
        <p className="eyebrow">Bắt đầu với HealthGuard</p>
        <h1>Tạo tài khoản</h1>
        <p>Đăng ký trước, sau đó bạn sẽ tạo nhóm chăm sóc trong một bước riêng.</p>
      </div>
      {serverError ? <ErrorNotice message={serverError} /> : null}
      <form className="form-grid" onSubmit={handleSubmit(onSubmit)} noValidate>
        <Field className="form-grid__full" label="Họ và tên" autoComplete="name" placeholder="Nguyễn Văn An" error={errors.full_name?.message} {...register("full_name")} />
        <Field className="form-grid__full" label="Email" type="email" autoComplete="email" placeholder="ten@email.com" error={errors.email?.message} {...register("email")} />
        <Field label="Mật khẩu" type="password" autoComplete="new-password" hint="Ít nhất 10 ký tự" error={errors.password?.message} {...register("password")} />
        <Field label="Nhập lại mật khẩu" type="password" autoComplete="new-password" error={errors.confirm_password?.message} {...register("confirm_password")} />
        <Button type="submit" loading={isSubmitting} className="button--full form-grid__full">Tạo tài khoản</Button>
      </form>
      <p className="auth-switch">Đã có tài khoản? <Link href="/dang-nhap">Đăng nhập</Link></p>
    </div>
  );
}

const caregiverSchema = z.object(registrationFields).refine(
  (data) => data.password === data.confirm_password,
  matchingPasswords,
);
type CaregiverValues = z.infer<typeof caregiverSchema>;

export function CaregiverRegisterForm({ token }: { token: string }) {
  return <CaregiverInvitationForm key={token} token={token} />;
}

function CaregiverInvitationForm({ token }: { token: string }) {
  const router = useRouter();
  const { user, loading: authLoading, setUser } = useAuth();
  const [serverError, setServerError] = useState("");
  const [invitation, setInvitation] = useState<InvitationInspection | null>(null);
  const [inspectionError, setInspectionError] = useState("");
  const [inspecting, setInspecting] = useState(Boolean(token));
  const [accepting, setAccepting] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const { register, handleSubmit, setValue, formState: { errors, isSubmitting } } = useForm<CaregiverValues>({ resolver: zodResolver(caregiverSchema) });

  useEffect(() => {
    let active = true;

    if (!token) {
      return () => { active = false; };
    }

    authApi.inspectInvitation(token)
      .then((result) => {
        if (!active) return;
        if (!result.valid) { setInspectionError("Lời mời đã hết hạn hoặc bị thu hồi. Hãy nhờ chủ nhóm tạo lời mời mới."); return; }
        setInvitation(result);
        setValue("email", result.email, { shouldValidate: true });
      })
      .catch((error) => {
        if (active) setInspectionError(getErrorMessage(error));
      })
      .finally(() => {
        if (active) setInspecting(false);
      });

    return () => { active = false; };
  }, [setValue, token]);

  async function onSubmit(values: CaregiverValues) {
    if (!token) return setServerError("Liên kết mời không hợp lệ hoặc đã thiếu mã mời.");
    setServerError("");
    try {
      const user = await authApi.registerCaregiver({
        full_name: values.full_name,
        email: values.email,
        password: values.password,
        invitation_token: token,
      });
      setUser(user);
      router.replace("/hom-nay");
    } catch (error) {
      setServerError(getErrorMessage(error));
    }
  }

  async function acceptWithCurrentAccount() {
    if (!invitation || !token || !user || accepting) return;
    setAccepting(true);
    setServerError("");
    try {
      await authApi.acceptInvitation(token);
      const updatedUser = await authApi.me();
      setUser(updatedUser);
      router.replace("/hom-nay");
    } catch (error) {
      setServerError(getErrorMessage(error));
    } finally {
      setAccepting(false);
    }
  }

  async function signOut() {
    if (signingOut) return;
    setSigningOut(true); setServerError("");
    try { await authApi.logout(); setUser(null); }
    catch (caught) { setServerError(getErrorMessage(caught)); }
    finally { setSigningOut(false); }
  }

  return (
    <div className="auth-card auth-card--wide">
      <div className="auth-heading">
        <p className="eyebrow">Lời mời chăm sóc</p>
        <h1>Tạo tài khoản người chăm sóc</h1>
        <p>Hệ thống kiểm tra lời mời trước khi cho phép bạn tạo tài khoản.</p>
      </div>
      {!token ? <ErrorNotice message="Liên kết đang thiếu mã mời. Hãy mở lại liên kết do chủ gia đình gửi." /> : null}
      {inspecting || authLoading ? (
        <div className="info-box" role="status"><LoaderCircle className="spin" size={18} /><p>Đang kiểm tra lời mời...</p></div>
      ) : null}
      {inspectionError ? <ErrorNotice message={inspectionError} /> : null}
      {invitation ? (
        <div className="info-box">
          <Mail size={18} />
          <p>
            Bạn được mời vào nhóm <strong>{invitation.care_group_name}</strong> bằng email <strong>{invitation.email}</strong>.
            Lời mời hết hạn lúc {new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short", timeZone: "Asia/Ho_Chi_Minh" }).format(new Date(invitation.expires_at))}.
          </p>
        </div>
      ) : null}
      {serverError ? <ErrorNotice message={serverError} /> : null}
      {invitation && !authLoading && user ? (
        <div className="form-stack">
          <div className="info-box">
            <Mail size={18} />
            <p>Bạn đang đăng nhập bằng <strong>{user.email}</strong>.</p>
          </div>
          {user.email.toLowerCase() !== invitation.email.toLowerCase() ? (
            <><ErrorNotice message="Email tài khoản đang đăng nhập không khớp email được mời. Hãy đăng xuất và đăng nhập đúng tài khoản." /><Button type="button" variant="secondary" loading={signingOut} onClick={signOut}>Đăng xuất để dùng đúng tài khoản</Button></>
          ) : user.current_group ? (
            <ErrorNotice message="Tài khoản này đã thuộc một nhóm chăm sóc. Mỗi tài khoản hiện chỉ tham gia một nhóm." />
          ) : (
            <Button type="button" loading={accepting} onClick={acceptWithCurrentAccount} className="button--full">
              Tham gia bằng tài khoản hiện tại
            </Button>
          )}
        </div>
      ) : invitation && !authLoading ? (
        <form className="form-grid" onSubmit={handleSubmit(onSubmit)} noValidate>
          <Field label="Họ và tên" autoComplete="name" error={errors.full_name?.message} {...register("full_name")} />
          <Field label="Email được mời" type="email" autoComplete="email" readOnly hint="Email này được khóa theo lời mời của chủ gia đình." error={errors.email?.message} {...register("email")} />
          <Field label="Mật khẩu" type="password" autoComplete="new-password" error={errors.password?.message} {...register("password")} />
          <Field label="Nhập lại mật khẩu" type="password" autoComplete="new-password" error={errors.confirm_password?.message} {...register("confirm_password")} />
          <Button type="submit" loading={isSubmitting} className="button--full form-grid__full">Chấp nhận lời mời</Button>
          <p className="auth-switch form-grid__full">
            Đã có tài khoản? <Link href={`/dang-nhap?next=${encodeURIComponent(`/tham-gia?token=${token}`)}`}>Đăng nhập để tham gia</Link>
          </p>
        </form>
      ) : null}
    </div>
  );
}
