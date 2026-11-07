import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CircleAlertIcon, ServerIcon } from 'lucide-react'

import { ThemeToggle } from '@/components/theme-toggle'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { api, ApiError } from '@/lib/api'

const MESSAGES: Record<string, [string, string]> = {
  bad_credentials: ['That didn’t work', 'Check your username and password, then try again.'],
  too_many_attempts: ['Too many tries', 'Wait a few minutes, then try again.'],
  not_allowed: ['This account can’t use this app', 'If you need it, ask IT.'],
}
const UNAVAILABLE: [string, string] = [
  'Sign-in isn’t available right now',
  'The server didn’t answer. Try again in a moment; if it keeps happening, ask IT.',
]

export function SignIn({ serverLabel, onSignedIn }: { serverLabel: string; onSignedIn: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<[string, string] | null>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    document.title = `Sign in · ${serverLabel}`
  }, [serverLabel])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(username, password)
      onSignedIn()
    } catch (err) {
      setError((err instanceof ApiError && MESSAGES[err.code]) || UNAVAILABLE)
      setPassword('')
      setBusy(false)
      passwordRef.current?.focus()
    }
  }

  return (
    <div className="relative flex h-dvh items-center justify-center bg-subtle px-4 pb-20">
      <ThemeToggle className="absolute top-2 right-4" />
      <Card className="w-full max-w-90 gap-5 p-6">
        <CardHeader className="gap-1.5 p-0">
          <div className="flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
            <ServerIcon className="size-3.5" aria-hidden />
            {serverLabel}
          </div>
          <CardTitle className="text-xl font-semibold tracking-tight">Sign in</CardTitle>
          <CardDescription className="text-[13px]">Use the same username and password as the file server.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-5 p-0">
          {error && (
            <Alert variant="destructive">
              <CircleAlertIcon aria-hidden />
              <AlertTitle>{error[0]}</AlertTitle>
              <AlertDescription>{error[1]}</AlertDescription>
            </Alert>
          )}
          <form onSubmit={submit}>
            <FieldGroup className="gap-3.5">
              <Field className="gap-1.5">
                <FieldLabel htmlFor="username">Username</FieldLabel>
                <Input
                  id="username"
                  autoFocus
                  required
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </Field>
              <Field className="gap-1.5">
                <FieldLabel htmlFor="password">Password</FieldLabel>
                <Input
                  ref={passwordRef}
                  id="password"
                  type="password"
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
              <Button type="submit" className="mt-1.5 w-full" disabled={busy}>
                {busy ? 'Signing in…' : 'Sign in'}
              </Button>
            </FieldGroup>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
