import NextAuth from "next-auth"
import Google from "next-auth/providers/google"
import Resend from "next-auth/providers/resend"

export const { handlers, signIn, signOut, auth } = NextAuth({
    providers: [
        Google({
            clientId: process.env.GOOGLE_CLIENT_ID,
            clientSecret: process.env.GOOGLE_CLIENT_SECRET,
        }),
        Resend({
            apiKey: process.env.RESEND_API_KEY,
            from: "onboarding@resend.dev"
        })
    ],
    pages: {
        signIn: "/login",
    },
    theme: {
        colorScheme: "dark",
        brandColor: "#ccff00", // Neon
    }
})
