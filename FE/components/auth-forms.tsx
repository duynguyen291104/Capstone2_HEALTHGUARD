"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Eye, EyeOff, Mail } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useAuth } from "@/components/auth-provider";
import { Button, ErrorNotice, Field } from "@/components/ui";
import { authApi, getErrorMessage } from "@/lib/api";

const loginSchema = z.object({
  email: z.string().trim().email("Email chưa đúng định dạng."),
  password: z.string().min(1, "Hãy nhập mật khẩu."),
});

type LoginValues = z.infer<typeof loginSchema>;

export function LoginForm() {
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
      router.replace("/hom-nay");
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
      <p className="auth-switch">Chưa có tài khoản? <Link href="/dang-ky">Tạo nhóm chăm sóc</Link></p>
      <div className="auth-help"><Mail size={17} /><span>Người chăm sóc cần mở đúng liên kết mời do chủ gia đình gửi.</span></div>
    </div>
  );
}

const registrationFields = {
  full_name: z.string().trim().min(2, "Hãy nhập họ tên."),
  email: z.string().trim().email("Email chưa đúng định dạng."),
  password: z.string().min(10, "Mật khẩu cần ít nhất 10 ký tự."),
  confirm_password: z.string(),
};

const matchingPasswords = {
  message: "Hai mật khẩu chưa trùng nhau.",
  path: ["confirm_password"],
};

const ownerSchema = z.object({
  ...registrationFields,
  care_group_name: z.string().trim().min(2, "Hãy đặt tên nhóm chăm sóc."),
}).refine((data) => data.password === data.confirm_password, matchingPasswords);

type OwnerValues = z.infer<typeof ownerSchema>;

export function OwnerRegisterForm() {
  const router = useRouter();
  const { setUser } = useAuth();
  const [serverError, setServerError] = useState("");
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<OwnerValues>({ resolver: zodResolver(ownerSchema) });

  async function onSubmit(values: OwnerValues) {
    setServerError("");
    try {
      const user = await authApi.registerOwner({
        full_name: values.full_name,
        email: values.email,
        password: values.password,
        care_group_name: values.care_group_name,
      });
      setUser(user);
      router.replace("/nguoi-duoc-cham-soc?welcome=1");
    } catch (error) {
      setServerError(getErrorMessage(error));
    }
  }

  return (
    <div className="auth-card auth-card--wide">
      <div className="auth-heading">
        <p className="eyebrow">Dành cho chủ gia đình</p>
        <h1>Tạo nhóm chăm sóc</h1>
        <p>Sau khi đăng ký, bạn có thể thêm bố mẹ và mời người chăm sóc.</p>
      </div>
      {serverError ? <ErrorNotice message={serverError} /> : null}
      <form className="form-grid" onSubmit={handleSubmit(onSubmit)} noValidate>
        <Field label="Họ và tên" autoComplete="name" placeholder="Nguyễn Văn An" error={errors.full_name?.message} {...register("full_name")} />
        <Field label="Tên nhóm chăm sóc" placeholder="Gia đình anh An" error={errors.care_group_name?.message} {...register("care_group_name")} />
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
  const router = useRouter();
  const { setUser } = useAuth();
  const [serverError, setServerError] = useState("");
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<CaregiverValues>({ resolver: zodResolver(caregiverSchema) });

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

  return (
    <div className="auth-card auth-card--wide">
      <div className="auth-heading">
        <p className="eyebrow">Lời mời chăm sóc</p>
        <h1>Tạo tài khoản người chăm sóc</h1>
        <p>Dùng email nhận lời mời để tham gia đúng nhóm gia đình.</p>
      </div>
      {!token ? <ErrorNotice message="Liên kết đang thiếu mã mời. Hãy mở lại liên kết do chủ gia đình gửi." /> : null}
      {serverError ? <ErrorNotice message={serverError} /> : null}
      <form className="form-grid" onSubmit={handleSubmit(onSubmit)} noValidate>
        <Field label="Họ và tên" autoComplete="name" error={errors.full_name?.message} {...register("full_name")} />
        <Field label="Email được mời" type="email" autoComplete="email" error={errors.email?.message} {...register("email")} />
        <Field label="Mật khẩu" type="password" autoComplete="new-password" error={errors.password?.message} {...register("password")} />
        <Field label="Nhập lại mật khẩu" type="password" autoComplete="new-password" error={errors.confirm_password?.message} {...register("confirm_password")} />
        <Button type="submit" loading={isSubmitting} disabled={!token} className="button--full form-grid__full">Chấp nhận lời mời</Button>
      </form>
    </div>
  );
}
